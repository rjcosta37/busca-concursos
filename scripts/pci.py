"""Cliente mínimo para o servidor MCP público da PCI Concursos.

O endpoint aceita JSON-RPC 2.0 direto por HTTP POST, sem autenticação e sem
session id. Documentação: https://www.pciconcursos.com.br/mcp-e-gpt
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

ENDPOINT = "https://mcp.pciconcursos.com.br/mcp"
TIMEOUT = 60


class PCIError(RuntimeError):
    pass


class PCIClient:
    def __init__(self, endpoint: str = ENDPOINT, timeout: int = TIMEOUT):
        self.endpoint = endpoint
        self.timeout = timeout
        self._id = 0

    def _rpc(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        payload = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            payload["params"] = params
        req = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.URLError as exc:
            raise PCIError(f"falha ao chamar {method}: {exc}") from exc

        data = json.loads(body)
        if "error" in data:
            raise PCIError(f"{method}: {data['error']}")
        return data.get("result", {})

    def call_tool(self, name: str, arguments: dict) -> dict:
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        for block in result.get("content", []):
            if block.get("type") == "text":
                try:
                    return json.loads(block["text"])
                except json.JSONDecodeError:
                    return {"message": block["text"], "data": []}
        return {"data": []}

    def por_cidade(self, uf: str, cidade: str) -> dict:
        return self.call_tool("buscar_por_cidade", {"uf": uf.lower(), "cidade": cidade})

    def por_cargo(self, cargo: str, uf: str | None = None) -> dict:
        args: dict = {"cargo": cargo}
        if uf:
            args["uf"] = uf.lower()
        return self.call_tool("buscar_por_cargo", args)

    def pesquisar(self, termo: str, uf: str | None = None) -> dict:
        args: dict = {"termo": termo}
        if uf:
            args["uf"] = uf.lower()
        return self.call_tool("pesquisar_concursos", args)
