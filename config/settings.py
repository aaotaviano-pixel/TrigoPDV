"""Leitura segura e centralizada das configurações locais do PDV.

As configurações ficam em ``config.ini`` para facilitar o ajuste no Windows,
mas nunca são consideradas confiáveis sem validação. Caminhos relativos são
resolvidos a partir da raiz do projeto, não do diretório de trabalho atual.
"""

from __future__ import annotations

from configparser import ConfigParser, NoOptionError, NoSectionError
from dataclasses import dataclass
import os
from pathlib import Path
import sys
from typing import Optional
from urllib.parse import urlsplit


APP_NAME = "TrigoPDV"
DATA_DIRECTORY_ENV = "TRIGOPDV_DATA_DIR"
PILOT_UPDATE_URL = "https://aaotaviano-pixel.github.io/TrigoPDV/updates"


def _resource_root() -> Path:
    """Return the directory containing bundled read-only application files."""

    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def _default_config_path() -> Path:
    """Keep runtime data outside Program Files when the app is packaged."""

    if not getattr(sys, "frozen", False):
        return _resource_root() / "config.ini"
    configured = os.environ.get(DATA_DIRECTORY_ENV, "").strip()
    data_root = Path(configured) if configured else Path(
        os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
    ) / APP_NAME
    return data_root / "config.ini"


PROJECT_ROOT = _resource_root()
DEFAULT_CONFIG_PATH = _default_config_path()
EXAMPLE_CONFIG_PATH = PROJECT_ROOT / "config.ini.example"


class ConfigurationError(ValueError):
    """Indica configuração ausente ou inválida."""


@dataclass(frozen=True)
class Settings:
    """Valores já validados usados pelas camadas de negócio."""

    project_root: Path
    database_path: Path
    backup_path: Path
    establishment_name: str
    establishment_document: str
    receipt_header: str
    pix_key: str
    pix_receiver_name: str
    pix_city: str
    printer_name: str
    printer_port: str
    open_food_facts_timeout: float = 3.0
    cosmos_api_token: str = ""
    cosmos_user_agent: str = ""
    printer_enabled: bool = False
    printer_mode: str = "DESATIVADA"
    printer_paper_width: int = 80
    printer_driver: str = "win32raw"
    printer_host: str = ""
    printer_queue_dir: Path = Path("data/print_queue")
    printer_uri: str = ""
    cut_paper: bool = True
    show_expected_to_operator: bool = False
    config_path: Path | None = None
    updates_enabled: bool = False
    update_channel: str = "stable"
    update_base_url: str = ""
    update_check_interval_hours: int = 6
    update_state_path: Path = Path("data/updates/update-state.json")
    resource_directory: Path = PROJECT_ROOT

    @property
    def data_directory(self) -> Path:
        return self.database_path.parent


def _as_path(project_root: Path, value: str, field_name: str) -> Path:
    value = (value or "").strip()
    if not value:
        raise ConfigurationError(f"A configuração '{field_name}' não pode ficar vazia.")
    path = Path(value).expanduser()
    return path if path.is_absolute() else (project_root / path).resolve()


def _get(parser: ConfigParser, section: str, option: str, default: Optional[str] = None) -> str:
    try:
        return parser.get(section, option)
    except (NoSectionError, NoOptionError):
        if default is not None:
            return default
        raise ConfigurationError(f"Falta a configuração [{section}] {option}.") from None


def ensure_default_config(config_path: Path | str = DEFAULT_CONFIG_PATH) -> Path:
    """Cria ``config.ini`` a partir do exemplo somente quando ele não existe.

    Não sobrescreve valores definidos pelo estabelecimento.
    """

    destination = Path(config_path)
    if destination.exists():
        return destination
    if not EXAMPLE_CONFIG_PATH.exists():
        raise ConfigurationError(f"Arquivo de exemplo não encontrado: {EXAMPLE_CONFIG_PATH}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(EXAMPLE_CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    return destination


def load_settings(config_path: Path | str = DEFAULT_CONFIG_PATH, *, create_if_missing: bool = True) -> Settings:
    """Carrega e valida as configurações.

    A criação automática só ocorre no primeiro início; erros de sintaxe ou de
    tipo são retornados com uma mensagem apropriada ao operador.
    """

    path = Path(config_path)
    if create_if_missing and not path.exists():
        ensure_default_config(path)
    if not path.exists():
        raise ConfigurationError(f"Arquivo de configuração não encontrado: {path}")

    parser = ConfigParser(interpolation=None)
    try:
        loaded = parser.read(path, encoding="utf-8")
    except OSError as exc:
        raise ConfigurationError(f"Não foi possível ler a configuração: {exc}") from exc
    if not loaded:
        raise ConfigurationError(f"Não foi possível carregar a configuração: {path}")

    project_root = path.resolve().parent
    try:
        timeout = parser.getfloat("integrations", "open_food_facts_timeout", fallback=3.0)
    except ValueError as exc:
        raise ConfigurationError("O timeout do Open Food Facts deve ser numérico.") from exc
    if timeout <= 0 or timeout > 30:
        raise ConfigurationError("O timeout do Open Food Facts deve estar entre 0 e 30 segundos.")
    cosmos_api_token = _get(parser, "integrations", "cosmos_api_token", "").strip()
    cosmos_user_agent = _get(parser, "integrations", "cosmos_user_agent", "").strip()
    if bool(cosmos_api_token) != bool(cosmos_user_agent):
        raise ConfigurationError(
            "Para usar a consulta Cosmos, configure token e User-Agent juntos em [integrations]."
        )

    try:
        printer_enabled = parser.getboolean("printing", "enabled", fallback=False)
    except ValueError as exc:
        raise ConfigurationError("A configuração [printing] enabled deve ser true ou false.") from exc
    configured_printer_name = _get(parser, "printing", "printer_name", "").strip()
    legacy_mode = (
        "DESATIVADA"
        if not printer_enabled
        else ("SELECIONADA" if configured_printer_name else "PADRAO_WINDOWS")
    )
    printer_mode = _get(parser, "printing", "mode", legacy_mode).strip().upper()
    if printer_mode not in {"SELECIONADA", "PADRAO_WINDOWS", "DESATIVADA"}:
        raise ConfigurationError(
            "O modo de impressão deve ser SELECIONADA, PADRAO_WINDOWS ou DESATIVADA."
        )
    printer_enabled = printer_mode != "DESATIVADA"
    if printer_mode != "SELECIONADA":
        configured_printer_name = ""
    try:
        cut_paper = parser.getboolean("printing", "cut_paper", fallback=True)
    except ValueError as exc:
        raise ConfigurationError("A configuração [printing] cut_paper deve ser true ou false.") from exc
    try:
        printer_paper_width = parser.getint("printing", "paper_width", fallback=80)
    except ValueError as exc:
        raise ConfigurationError("A largura do papel deve ser 58 ou 80 mm.") from exc
    if printer_paper_width not in {58, 80}:
        raise ConfigurationError("A largura do papel deve ser 58 ou 80 mm.")
    try:
        show_expected_to_operator = parser.getboolean("cash", "show_expected_to_operator", fallback=False)
    except ValueError as exc:
        raise ConfigurationError("A configuração [cash] show_expected_to_operator deve ser true ou false.") from exc
    try:
        updates_enabled = parser.getboolean("updates", "enabled", fallback=False)
        update_check_interval_hours = parser.getint("updates", "check_interval_hours", fallback=6)
    except ValueError as exc:
        raise ConfigurationError("As configurações de atualização são inválidas.") from exc
    update_channel = _get(parser, "updates", "channel", "stable").strip().lower() or "stable"
    update_base_url = _get(parser, "updates", "base_url", "").strip()
    # Nunca transforme `enabled = false` em consentimento implícito. Instalações
    # novas recebem o piloto pelo arquivo de exemplo; uma escolha persistida de
    # desativação continua desativada depois de atualizar o executável.
    if update_channel not in {"internal", "pilot", "stable"}:
        raise ConfigurationError("O canal de atualização deve ser internal, pilot ou stable.")
    if not 1 <= update_check_interval_hours <= 168:
        raise ConfigurationError("O intervalo de atualização deve ficar entre 1 e 168 horas.")
    if updates_enabled:
        parsed_update_url = urlsplit(update_base_url)
        if (
            parsed_update_url.scheme.lower() != "https"
            or not parsed_update_url.hostname
            or parsed_update_url.username is not None
            or parsed_update_url.password is not None
            or parsed_update_url.query
            or parsed_update_url.fragment
        ):
            raise ConfigurationError("Atualizações online exigem um endereço HTTPS seguro.")
    printer_driver = _get(parser, "printing", "driver", "win32raw").strip().lower() or "win32raw"
    if printer_driver not in {"win32raw", "network", "ipp"}:
        raise ConfigurationError("O driver de impressão deve ser 'win32raw', 'network' ou 'ipp'.")
    printer_port = _get(parser, "printing", "printer_port", "9100").strip() or "9100"
    try:
        port_number = int(printer_port)
    except ValueError as exc:
        raise ConfigurationError("A porta da impressora deve ser numérica.") from exc
    if not 1 <= port_number <= 65535:
        raise ConfigurationError("A porta da impressora deve estar entre 1 e 65535.")

    return Settings(
        project_root=project_root,
        database_path=_as_path(project_root, _get(parser, "paths", "database_path"), "database_path"),
        backup_path=_as_path(project_root, _get(parser, "paths", "backup_path"), "backup_path"),
        establishment_name=_get(parser, "store", "name", "TRIGO DE MINAS").strip() or "TRIGO DE MINAS",
        establishment_document=_get(parser, "store", "document", "").strip(),
        receipt_header=_get(parser, "store", "receipt_header", "PDV TRIGO DE MINAS").strip(),
        pix_key=_get(parser, "pix", "key", "").strip(),
        pix_receiver_name=_get(parser, "pix", "receiver_name", "TRIGO DE MINAS").strip() or "TRIGO DE MINAS",
        pix_city=_get(parser, "pix", "city", "SAO PAULO").strip() or "SAO PAULO",
        printer_name=configured_printer_name,
        printer_port=printer_port,
        open_food_facts_timeout=timeout,
        cosmos_api_token=cosmos_api_token,
        cosmos_user_agent=cosmos_user_agent,
        printer_enabled=printer_enabled,
        printer_mode=printer_mode,
        printer_paper_width=printer_paper_width,
        printer_driver=printer_driver,
        printer_host=_get(parser, "printing", "host", "").strip(),
        printer_queue_dir=_as_path(
            project_root,
            _get(parser, "printing", "queue_dir", "data/print_queue"),
            "queue_dir",
        ),
        printer_uri=_get(parser, "printing", "uri", "").strip(),
        cut_paper=cut_paper,
        show_expected_to_operator=show_expected_to_operator,
        config_path=path.resolve(),
        updates_enabled=updates_enabled,
        update_channel=update_channel,
        update_base_url=update_base_url.rstrip("/"),
        update_check_interval_hours=update_check_interval_hours,
        update_state_path=_as_path(
            project_root,
            _get(parser, "updates", "state_path", "data/updates/update-state.json"),
            "update_state_path",
        ),
        resource_directory=PROJECT_ROOT,
    )


def save_printer_settings(
    config_path: Path | str | None,
    *,
    printer_name: str,
    enabled: bool,
    driver: str = "win32raw",
    host: str = "",
    printer_port: str = "9100",
    mode: str | None = None,
    paper_width: int | None = None,
    uri: str = "",
) -> Path | None:
    """Persist printer selection without exposing or rewriting credentials.

    ``ConfigParser`` preserves every value, including local integration
    settings, while writing through a temporary file prevents a half-written
    configuration after a power loss.
    """

    if config_path is None:
        return None
    path = Path(config_path)
    parser = ConfigParser(interpolation=None)
    if path.exists():
        parser.read(path, encoding="utf-8")
    elif EXAMPLE_CONFIG_PATH.exists():
        parser.read(EXAMPLE_CONFIG_PATH, encoding="utf-8")
    if not parser.has_section("printing"):
        parser.add_section("printing")
    selected_mode = str(
        mode
        or (
            "DESATIVADA"
            if not enabled
            else ("SELECIONADA" if str(printer_name or "").strip() else "PADRAO_WINDOWS")
        )
    ).strip().upper()
    if selected_mode not in {"SELECIONADA", "PADRAO_WINDOWS", "DESATIVADA"}:
        raise ConfigurationError("Modo de impressão inválido.")
    if selected_mode != "SELECIONADA":
        printer_name = ""
    enabled = selected_mode != "DESATIVADA"
    parser.set("printing", "enabled", "true" if enabled else "false")
    parser.set("printing", "mode", selected_mode)
    parser.set("printing", "driver", str(driver or "win32raw").strip().lower() or "win32raw")
    parser.set("printing", "printer_name", str(printer_name or "").strip())
    parser.set("printing", "uri", str(uri or "").strip())
    parser.set("printing", "host", str(host or "").strip())
    parser.set("printing", "printer_port", str(printer_port or "9100").strip())
    if paper_width is not None:
        try:
            normalized_paper_width = int(paper_width)
        except (TypeError, ValueError) as exc:
            raise ConfigurationError("A largura do papel deve ser 58 ou 80 mm.") from exc
        if normalized_paper_width not in {58, 80}:
            raise ConfigurationError("A largura do papel deve ser 58 ou 80 mm.")
        parser.set("printing", "paper_width", str(normalized_paper_width))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        parser.write(handle)
    os.replace(temporary, path)
    return path
