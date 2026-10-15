"""Local trust sockets must remain inaccessible to other OS users."""
import importlib.util
from pathlib import Path
import stat


def load_local_db():
    source = Path(__file__).resolve().parents[1] / 'scripts/local_db.py'
    spec = importlib.util.spec_from_file_location('local_db_permissions_test', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_existing_runtime_and_logs_become_private_without_losing_data(tmp_path):
    runtime = tmp_path / '.runtime'
    sockets = runtime / 'pg_socket'
    sockets.mkdir(parents=True, mode=0o755)
    runtime.chmod(0o755)
    log = runtime / 'api.log'
    log.write_text('keep existing log')
    log.chmod(0o644)
    module = load_local_db()
    module.RUNTIME, module.SOCKETS = runtime, sockets
    module.secure_runtime_paths()
    assert stat.S_IMODE(runtime.stat().st_mode) == 0o700
    assert stat.S_IMODE(sockets.stat().st_mode) == 0o700
    assert log.read_text() == 'keep existing log'
    for name in ('api.log', 'worker.log', 'postgres.log'):
        assert stat.S_IMODE((runtime / name).stat().st_mode) == 0o600


def test_first_run_creates_private_runtime_and_socket_directory(tmp_path):
    module = load_local_db()
    module.RUNTIME = tmp_path / '.runtime'
    module.SOCKETS = module.RUNTIME / 'pg_socket'
    module.secure_runtime_paths()
    assert stat.S_IMODE(module.RUNTIME.stat().st_mode) == 0o700
    assert stat.S_IMODE(module.SOCKETS.stat().st_mode) == 0o700
