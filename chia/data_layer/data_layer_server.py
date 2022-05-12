import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

import aiohttp
import aiosqlite
from aiohttp import web  # lgtm [py/import and import from]

from chia.data_layer.data_layer_types import InsertionData, TerminalNode
from chia.data_layer.data_store import DataStore, create_db_wrapper
from chia.server.upnp import UPnP
from chia.types.blockchain_format.tree_hash import bytes32
from chia.util.db_wrapper import DBWrapper2


@dataclass
class DataLayerServer:
    config: Dict[str, Any]
    db_path: Path
    log: logging.Logger

    async def start(self) -> None:
        self.log.info("Starting Data Layer Server.")
        self.db_wrapper = await create_db_wrapper(self.db_path)
        self.data_store = await DataStore.create(db_wrapper=self.db_wrapper)
        self.port = self.config["host_port"]

        # Setup UPnP for the data_layer_service port
        self.upnp: UPnP = UPnP()  # type: ignore[no-untyped-call]
        self.upnp.remap(self.port)  # type: ignore[no-untyped-call]

        app = web.Application()
        app.router.add_route("GET", "/ws", self.websocket_handler)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, self.config["host_ip"], port=self.port)
        await self.site.start()
        self.log.info("Started Data Layer Server.")

    async def stop(self) -> None:

        self.upnp.release(self.port)  # type: ignore[no-untyped-call]
        # this is a blocking call, waiting for the UPnP thread to exit
        self.upnp.shutdown()  # type: ignore[no-untyped-call]

        self.log.info("Stopped Data Layer Server.")
        await self.runner.cleanup()

    async def handle_tree_root(self, request: Dict[str, str]) -> str:
        tree_id = request["tree_id"]
        requested_hash = request["node_hash"]
        tree_id_bytes = bytes32.from_hexstr(tree_id)
        requested_hash_bytes = bytes32.from_hexstr(requested_hash)
        tree_root = await self.data_store.get_last_tree_root_by_hash(tree_id_bytes, requested_hash_bytes)
        if tree_root is None or tree_root.node_hash is None:
            return json.dumps({})
        result = {
            "tree_id": tree_id,
            "generation": tree_root.generation,
            "node_hash": tree_root.node_hash.hex(),
            "status": tree_root.status.value,
        }
        return json.dumps(result)

    async def handle_tree_nodes(self, request: Dict[str, str]) -> str:
        node_hash = request["node_hash"]
        tree_id = request["tree_id"]
        node_hash_bytes = bytes32.from_hexstr(node_hash)
        tree_id_bytes = bytes32.from_hexstr(tree_id)
        nodes = await self.data_store.get_left_to_right_ordering(node_hash_bytes, tree_id_bytes, True)
        answer = []
        for node in nodes:
            if isinstance(node, TerminalNode):
                answer.append(
                    {
                        "key": node.key.hex(),
                        "value": node.value.hex(),
                        "is_terminal": True,
                    }
                )
            else:
                answer.append(
                    {
                        "left": str(node.left_hash),
                        "right": str(node.right_hash),
                        "is_terminal": False,
                    }
                )
        return json.dumps(
            {
                "answer": answer,
            }
        )

    async def handle_operations(self, request: Dict[str, str]) -> str:
        tree_id = request["tree_id"]
        generation = request["generation"]
        max_generation = request["max_generation"]
        tree_id_bytes = bytes32.from_hexstr(tree_id)
        operations_data = await self.data_store.get_operations(tree_id_bytes, int(generation), int(max_generation))
        answer = []
        for operation in operations_data:
            if isinstance(operation, InsertionData):
                reference_node_hash = (
                    "None" if operation.reference_node_hash is None else operation.reference_node_hash.hex()
                )
                side = "None" if operation.side is None else operation.side.value
                answer.append(
                    {
                        "is_insert": True,
                        "hash": operation.hash.hex(),
                        "key": operation.key.hex(),
                        "value": operation.value.hex(),
                        "reference_node_hash": reference_node_hash,
                        "side": side,
                        "root_status": operation.root_status.value,
                    }
                )
            else:
                answer.append(
                    {
                        "is_insert": False,
                        "hash": "None" if operation.hash is None else operation.hash.hex(),
                        "key": operation.key.hex(),
                        "root_status": operation.root_status.value,
                    }
                )

        return json.dumps(answer)

    async def websocket_handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = aiohttp.web.WebSocketResponse(max_msg_size=0)
        await ws.prepare(request)

        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                json_request = json.loads(msg.data)
                if json_request["type"] == "close":
                    await ws.close()
                    return ws
                elif json_request["type"] == "request_root":
                    json_response = await self.handle_tree_root(json_request)
                elif json_request["type"] == "request_nodes":
                    json_response = await self.handle_tree_nodes(json_request)
                elif json_request["type"] == "request_operations":
                    json_response = await self.handle_operations(json_request)
                await ws.send_str(json_response)

        return ws
