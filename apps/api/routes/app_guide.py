"""Public MCP tool for the onFlows product guide."""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter
from mcp import types
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings


INSTRUCTIONS = (
    "Използвай справочника за въпроси за възможностите и показателите на onFlows. "
    "За индивидуална справка са нужни данни за конкретния спортист и период. "
    "При въпрос за хранене търси дневната хранителна цел. "
    "Представяй моделните оценки и прогнозите като такива. "
    "Съобщавай за извършена промяна само при потвърден запис от приложението. "
    "Този инструмент предоставя единствено описание на приложението."
)
GUIDE_FILE = Path(__file__).resolve().parents[1] / "onflows_guide.md"


@asynccontextmanager
async def guide_lifespan(app):
    server = FastMCP(
        "onFlows", instructions=INSTRUCTIONS, stateless_http=True, json_response=True,
        max_request_body_size=32768, log_level="WARNING",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["onflows-api-staging.onrender.com", "localhost", "localhost:*",
                           "127.0.0.1", "127.0.0.1:*"],
            allowed_origins=["https://chatgpt.com", "https://onflows-api-staging.onrender.com"],
        ),
    )
    guide = GUIDE_FILE.read_text(encoding="utf-8")

    @server.tool(title="Справочник за onFlows", description=(
        "Обяснява функциите на onFlows, показателите, мерните единици и правата в приложението. "
        "Съдържа само обща информация."
    ), annotations=types.ToolAnnotations(readOnlyHint=True, openWorldHint=False),
        meta={"securitySchemes": [{"type": "noauth"}]}, structured_output=False)
    def get_app_guide() -> str:
        return guide

    transport = server.streamable_http_app()
    async with server.session_manager.run():
        yield {"onflows_guide_transport": transport}


class GuideEndpoint:
    async def __call__(self, scope, receive, send):
        await scope["state"]["onflows_guide_transport"](scope, receive, send)


router = APIRouter(lifespan=guide_lifespan)
router.add_route("/mcp", GuideEndpoint(), methods=["GET", "POST", "DELETE"], include_in_schema=False)
