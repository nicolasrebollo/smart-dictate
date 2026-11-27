"""This computer's processor (local mode without a graphics card): the settings it implies, the setup
choices offered, and what the installer runs for each variant (nothing is downloaded here)."""

import json

import pytest

from dictate import local_gpu, models, modes
from dictate.app import mode_options
from dictate.config import CPU_CLEANUP_MODEL, ConfigError, local_device, parse_config

CPU = '[client]\nmode = "local"\nlocal_device = "cpu"\n'


def test_the_processor_variant_picks_its_engine() -> None:
    c = parse_config(CPU)
    assert (c.asr.device, c.asr.quantization, c.llm.build, c.llm.gpu_layers) == ("cpu", "int8", "cpu", 0)
    assert c.cleanup.model == CPU_CLEANUP_MODEL and c.cleanup.timeout_ms == 4000
    assert models.llama_server_exe(c).parent.name.endswith("-cpu")
    assert modes.describe(c) == "This computer's processor"


def test_the_graphics_card_variant_is_unchanged() -> None:
    c = parse_config('[client]\nmode = "local"\n')
    assert (c.asr.device, c.asr.quantization, c.llm.build, c.llm.gpu_layers) == ("cuda", "", "cuda", 99)
    assert c.cleanup.model == "gemma-4-e2b"
    assert models.llama_server_exe(c).parent.name.endswith("-cuda13")
    assert modes.describe(c) == "This computer's graphics card"


def test_what_you_set_yourself_wins() -> None:
    c = parse_config(CPU + '[server]\nmodel = "gemma-4-e2b"\n[asr]\nquantization = ""\n[cleanup]\ntimeout_ms = 9000\n')
    assert c.cleanup.model == "gemma-4-e2b" and c.asr.quantization == "" and c.cleanup.timeout_ms == 9000
    assert c.asr.device == "cpu"  # the rest still follows the processor variant


def test_the_runtime_follows_the_variant_it_was_started_for(monkeypatch) -> None:
    monkeypatch.setenv("DICTATE_LOCAL_DEVICE", "cpu")  # being installed: the settings don't say cpu yet
    c = parse_config('[client]\nmode = "remote"\n')
    assert local_device(c) == "cpu" and c.asr.device == "cpu" and c.llm.build == "cpu"


def test_bad_values_are_refused() -> None:
    with pytest.raises(ConfigError, match="local_device"):
        parse_config('[client]\nlocal_device = "npu"\n')
    with pytest.raises(ConfigError, match="build"):
        parse_config('[llm]\nbuild = "vulkan"\n')


def test_the_cpu_build_of_llama_cpp_is_pinned() -> None:
    cpu, cuda = models.LLAMA_CPP_BUILDS["cpu"], models.LLAMA_CPP_BUILDS["cuda"]
    assert cpu.url.endswith("-bin-win-cpu-x64.zip") and len(cpu.sha256) == 64
    assert cpu.folder != cuda.folder and cuda.sha256 == models.LLAMA_CPP_SHA256


def test_setup_offers_the_processor_on_any_pc() -> None:
    assert [k for k, _ in mode_options(None, True)] == ["remote", "cpu", "online"]
    assert [k for k, _ in mode_options(("RTX 4060", 8188), True)] == ["remote", "local", "cpu", "online"]
    assert [k for k, _ in mode_options(None, False)] == ["remote", "online"]  # not the Windows app


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    for name in ("uv.exe", "requirements.txt", "requirements-cpu.txt", "dictate-0.0.0-py3-none-any.whl"):
        (bundle / name).write_text("")
    monkeypatch.setenv("DICTATE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(local_gpu, "bundle_dir", lambda: bundle)
    ran: list[tuple[list[str], str]] = []

    def fake_run(args, log_file, report, what, start, end, device="gpu"):
        ran.append((args, device))
        if args[1:2] == ["venv"]:  # uv made the environment
            py = local_gpu._python(device)
            py.parent.mkdir(parents=True, exist_ok=True)
            py.write_text("")

    monkeypatch.setattr(local_gpu, "_run", fake_run)
    return ran


def test_each_variant_installs_into_its_own_environment(runtime) -> None:
    local_gpu.install(lambda *_: None, "cpu")
    assert local_gpu.installed("cpu") and not local_gpu.installed("gpu")
    pip = next(args for args, _ in runtime if "--require-hashes" in args)
    assert pip[-1].endswith("requirements-cpu.txt") and "venv-cpu" in pip[pip.index("--python") + 1]
    assert all(device == "cpu" for _, device in runtime)  # the setup step downloads the processor's models
    marker = json.loads(local_gpu._marker("cpu").read_text(encoding="utf-8"))
    assert marker["device"] == "cpu"

    runtime.clear()
    local_gpu.install(lambda *_: None, "gpu")
    pip = next(args for args, _ in runtime if "--require-hashes" in args)
    assert pip[-1].endswith("requirements.txt") and "venv-cpu" not in pip[pip.index("--python") + 1]
    assert local_gpu.installed("cpu") and local_gpu.installed("gpu")  # side by side


def test_the_runtime_is_told_its_variant() -> None:
    assert local_gpu._env("cpu")["DICTATE_LOCAL_DEVICE"] == "cpu"
    assert local_gpu._env()["DICTATE_LOCAL_DEVICE"] == "gpu"
    assert local_gpu._python("gpu").parent.parent.name == "venv"  # existing installs keep their folder


def test_a_newly_chosen_cleanup_model_triggers_the_setup(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DICTATE_DATA_DIR", str(tmp_path / "data"))
    cfg = parse_config(CPU)
    py = local_gpu._python("cpu")
    py.parent.mkdir(parents=True)
    py.write_text("")
    from dictate import __version__

    local_gpu._marker("cpu").write_text(json.dumps({"version": __version__, "device": "cpu"}), encoding="utf-8")
    present = cfg.__class__(**{**cfg.__dict__, "models_dir": local_gpu.models_dir()})
    for path in (
        models.silero_path(present),
        *(models.asr_dir(present) / f for f in ("config.json", "vocab.txt", "nemo128.onnx")),
        *(models.asr_dir(present) / f for f in models.ASR_MODELS[cfg.asr.model].variants["int8"]),
        models.llama_server_exe(present),
        models.llm_path(present),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
    assert local_gpu.ready(cfg)
    assert not local_gpu.ready(parse_config(CPU + '[server]\nmodel = "gemma-4-e2b"\n'))  # not downloaded yet
