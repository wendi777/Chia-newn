import contextlib
import os
import pathlib
import string
import subprocess
import sys
import sysconfig
import time
from typing import AsyncIterable, Awaitable, Callable, Iterator, List

import aiosqlite

# https://github.com/pytest-dev/pytest/issues/7469
from _pytest.fixtures import SubRequest
import pytest
import pytest_asyncio

from chia.data_layer.data_store import DataStore, create_db_wrapper
from chia.types.blockchain_format.tree_hash import bytes32
from chia.util.db_wrapper import DBWrapper2

from tests.core.data_layer.util import add_0123_example, add_01234567_example, ChiaRoot, Example


# TODO: These are more general than the data layer and should either move elsewhere or
#       be replaced with an existing common approach.  For now they can at least be
#       shared among the data layer test files.


@pytest.fixture(name="scripts_path", scope="session")
def scripts_path_fixture() -> pathlib.Path:
    scripts_string = sysconfig.get_path("scripts")
    if scripts_string is None:
        raise Exception("These tests depend on the scripts path existing")

    return pathlib.Path(scripts_string)


@pytest.fixture(name="chia_root", scope="function")
def chia_root_fixture(tmp_path: pathlib.Path, scripts_path: pathlib.Path) -> ChiaRoot:
    root = ChiaRoot(path=tmp_path.joinpath("chia_root"), scripts_path=scripts_path)
    root.run(args=["init"])
    root.run(args=["configure", "--set-log-level", "INFO"])

    return root


@contextlib.contextmanager
def closing_chia_root_popen(chia_root: ChiaRoot, args: List[str]) -> Iterator[None]:
    environment = {**os.environ, "CHIA_ROOT": os.fspath(chia_root.path)}

    with subprocess.Popen(args=args, env=environment) as process:
        try:
            yield
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()


@pytest.fixture(name="chia_daemon", scope="function")
def chia_daemon_fixture(chia_root: ChiaRoot) -> Iterator[None]:
    with closing_chia_root_popen(chia_root=chia_root, args=[sys.executable, "-m", "chia.daemon.server"]):
        # TODO: this is not pretty as a hard coded time
        # let it settle
        time.sleep(5)
        yield


@pytest.fixture(name="chia_data", scope="function")
def chia_data_fixture(chia_root: ChiaRoot, chia_daemon: None, scripts_path: pathlib.Path) -> Iterator[None]:
    with closing_chia_root_popen(chia_root=chia_root, args=[os.fspath(scripts_path.joinpath("chia_data_layer"))]):
        # TODO: this is not pretty as a hard coded time
        # let it settle
        time.sleep(5)
        yield


@pytest.fixture(name="create_example", params=[add_0123_example, add_01234567_example])
def create_example_fixture(request: SubRequest) -> Callable[[DataStore, bytes32], Awaitable[Example]]:
    return request.param  # type: ignore[no-any-return]


@pytest_asyncio.fixture(name="db_wrapper", scope="function")
async def db_wrapper_fixture(request: SubRequest) -> DBWrapper2:
    name = "".join(character for character in request.node.name if character in string.ascii_letters + string.digits)
    db_wrapper = await create_db_wrapper(f"file:memory_datalayer_test_fixture_{name}?mode=memory&cache=shared")
    yield db_wrapper
    await db_wrapper.close()


@pytest.fixture(name="tree_id", scope="function")
def tree_id_fixture() -> bytes32:
    base = b"a tree id"
    pad = b"." * (32 - len(base))
    return bytes32(pad + base)


@pytest_asyncio.fixture(name="raw_data_store", scope="function")
async def raw_data_store_fixture(db_wrapper: DBWrapper2) -> DataStore:
    return await DataStore.create(db_wrapper=db_wrapper)


@pytest_asyncio.fixture(name="data_store", scope="function")
async def data_store_fixture(raw_data_store: DataStore, tree_id: bytes32) -> AsyncIterable[DataStore]:
    await raw_data_store.create_tree(tree_id=tree_id)

    await raw_data_store.check()
    yield raw_data_store
    await raw_data_store.check()
