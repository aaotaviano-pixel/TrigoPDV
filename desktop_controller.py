"""Adapter between the Tk desktop UI and the session-aware application facade.

The business facade intentionally exposes concise domain APIs.  The desktop UI,
on the other hand, passes the active operator with every callback so accidental
cross-session calls are visible.  This adapter validates that boundary and is
the single place that triggers post-commit receipt printing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from config.settings import Settings, save_printer_settings
from printing.discovery import default_printer_name, list_windows_printers
from printing.ipp import transport_security
from printing.outbox import PrintOutboxWorker
from printing.receipt_printer import PrintResult, ReceiptPrinter
from services.errors import AuthenticationError, AuthorizationError, ValidationError
from services.pdv_service import PDVService
from updates.coordinator import UpdateCoordinator
from updates.event_log import UpdateEventLogger
from updates.models import UpdatePolicy
from updates.repository import TufRepository
from updates.state import UpdateStateStore
from updates.velopack_adapter import VelopackAdapter


class DesktopController:
    """UI-shaped controller that preserves domain authorization guarantees."""

    def __init__(self, service: PDVService, settings: Settings | None = None) -> None:
        self.service = service
        self.settings = settings or service.settings
        self.printer = ReceiptPrinter(
            {
                "enabled": self.settings.printer_enabled,
                "driver": self.settings.printer_driver,
                "printer_name": self.settings.printer_name,
                "host": self.settings.printer_host,
                "port": self.settings.printer_port,
                "queue_dir": self.settings.printer_queue_dir,
                "cut_paper": getattr(self.settings, "cut_paper", True),
                "paper_width": getattr(self.settings, "printer_paper_width", 80),
                "uri": getattr(self.settings, "printer_uri", ""),
            }
        )
        self.print_worker = PrintOutboxWorker(
            self.service.database, lambda: self.printer
        )
        self._pending_update_offer = None

    def _rebuild_printer(self) -> None:
        self.printer = ReceiptPrinter(
            {
                "enabled": self.settings.printer_enabled,
                "driver": self.settings.printer_driver,
                "printer_name": self.settings.printer_name,
                "host": self.settings.printer_host,
                "port": self.settings.printer_port,
                "queue_dir": self.settings.printer_queue_dir,
                "cut_paper": getattr(self.settings, "cut_paper", True),
                "paper_width": getattr(self.settings, "printer_paper_width", 80),
                "uri": getattr(self.settings, "printer_uri", ""),
            }
        )

    # -- impressora -------------------------------------------------------
    def list_printers(self) -> list[dict[str, Any]]:
        """Read the live Windows spooler list; no names are hard-coded."""

        self._require_admin()
        return list_windows_printers()

    discover_printers = list_printers

    def printer_configuration(self) -> dict[str, Any]:
        """Return selected/default/status information for the admin screen."""

        self._require_admin()
        printers = list_windows_printers()
        configured = str(self.settings.printer_name or "").strip()
        default = default_printer_name()
        mode = str(getattr(self.settings, "printer_mode", "") or "").strip().upper()
        if not self.settings.printer_enabled:
            mode = "DESATIVADA"
        elif not mode or mode == "DESATIVADA":
            mode = "DESATIVADA" if not self.settings.printer_enabled else (
                "SELECIONADA" if configured else "PADRAO_WINDOWS"
            )
        selected = next((item for item in printers if str(item.get("name", "")).casefold() == configured.casefold()), None) if configured else None
        effective = (
            configured
            if mode == "SELECIONADA"
            else (default if mode == "PADRAO_WINDOWS" else "")
        )
        driver = str(self.settings.printer_driver or "win32raw").strip().lower()
        transport = transport_security(self.settings.printer_uri) if driver == "ipp" else {
            "valid": True,
            "encrypted": None,
            "status": "Impressão gerenciada pelo Windows.",
            "warning": "",
        }
        if driver == "ipp":
            selected_found = bool(transport["valid"])
            selected_available = False
            status = str(transport["status"])
        else:
            default_item = next(
                (
                    item
                    for item in printers
                    if str(item.get("name", "")).casefold() == str(default or "").casefold()
                ),
                None,
            )
            if mode == "DESATIVADA":
                selected_found = False
                selected_available = False
                status = "Impressão desativada"
            elif mode == "PADRAO_WINDOWS":
                selected_found = bool(default)
                selected_available = bool(
                    default_item and default_item.get("available", False)
                )
                status = (
                    str(default_item.get("status") or "Disponível")
                    if default_item
                    else ("Não encontrada" if default else "Nenhuma impressora padrão")
                )
            else:
                selected_found = bool(selected)
                selected_available = bool(selected and selected.get("available", False))
                status = (selected or {}).get("status") if selected else "Não encontrada"
        return {
            "enabled": mode != "DESATIVADA",
            "mode": mode,
            "driver": driver,
            "configured_name": configured,
            "effective_name": effective,
            "default_name": default,
            "selected_found": selected_found,
            "selected_available": selected_available,
            "status": status,
            "transport_encrypted": transport["encrypted"],
            "transport_warning": transport["warning"],
            "paper_width": getattr(self.settings, "printer_paper_width", 80),
            "printers": printers,
        }

    get_printer_configuration = printer_configuration

    def save_printer_selection(self, printer_name: str) -> dict[str, Any]:
        """Persist one of the printers currently returned by Windows."""

        self._require_admin()
        name = str(printer_name or "").strip()
        if not name:
            # Último limite prático antes de persistir fora do SQLite.
            self._require_admin()
            save_printer_settings(
                getattr(self.settings, "config_path", None),
                printer_name="",
                enabled=False,
                driver="win32raw",
                host="",
                printer_port=self.settings.printer_port,
                mode="DESATIVADA",
            )
            self.settings = replace(self.settings, printer_name="", printer_enabled=False, printer_mode="DESATIVADA", printer_driver="win32raw", printer_host="")
            self.service.settings = self.settings
            self._rebuild_printer()
            return self.printer_configuration()
        printers = list_windows_printers()
        selected = next((item for item in printers if str(item.get("name", "")).casefold() == name.casefold()), None)
        if selected is None:
            raise ValidationError("A impressora não está instalada nesta máquina. Clique em Atualizar impressoras.")
        canonical_name = str(selected.get("name") or name)
        # A enumeração do spooler pode demorar; revalida depois dela sem manter
        # uma transação SQLite aberta durante o I/O externo.
        self._require_admin()
        save_printer_settings(
            getattr(self.settings, "config_path", None),
            printer_name=canonical_name,
            enabled=True,
            driver="win32raw",
            host="",
            printer_port=self.settings.printer_port,
            mode="SELECIONADA",
        )
        self.settings = replace(
            self.settings,
            printer_name=canonical_name,
            printer_enabled=True,
            printer_mode="SELECIONADA",
            printer_driver="win32raw",
            printer_host="",
        )
        self.service.settings = self.settings
        self._rebuild_printer()
        return self.printer_configuration()

    select_printer = save_printer_selection

    def use_default_printer(self) -> dict[str, Any]:
        self._require_admin()
        default = default_printer_name()
        if not default:
            raise ValidationError("O Windows não possui uma impressora padrão disponível.")
        self._require_admin()
        save_printer_settings(
            getattr(self.settings, "config_path", None),
            printer_name="",
            enabled=True,
            driver="win32raw",
            host="",
            printer_port=self.settings.printer_port,
            mode="PADRAO_WINDOWS",
        )
        self.settings = replace(
            self.settings,
            printer_name="",
            printer_enabled=True,
            printer_mode="PADRAO_WINDOWS",
            printer_driver="win32raw",
            printer_host="",
        )
        self.service.settings = self.settings
        self._rebuild_printer()
        return self.printer_configuration()

    def save_printer_paper_width(self, paper_width: int) -> dict[str, Any]:
        """Persist the thermal paper profile without changing printer choice."""

        self._require_admin()
        try:
            normalized = int(paper_width)
        except (TypeError, ValueError) as exc:
            raise ValidationError("A largura do papel deve ser 58 ou 80 mm.") from exc
        if normalized not in {58, 80}:
            raise ValidationError("A largura do papel deve ser 58 ou 80 mm.")
        self._require_admin()
        save_printer_settings(
            getattr(self.settings, "config_path", None),
            printer_name=self.settings.printer_name,
            enabled=self.settings.printer_enabled,
            driver=self.settings.printer_driver,
            host=self.settings.printer_host,
            printer_port=self.settings.printer_port,
            mode=self.settings.printer_mode,
            paper_width=normalized,
            uri=self.settings.printer_uri,
        )
        self.settings = replace(self.settings, printer_paper_width=normalized)
        self.service.settings = self.settings
        self._rebuild_printer()
        return self.printer_configuration()

    # -- session boundary -------------------------------------------------
    def authenticate(self, login: str, password: str) -> dict | None:
        return self.service.authenticate(login, password)

    def installation_status(self):
        return self.service.installation_status()

    def generate_recovery_code(self) -> str:
        return self.service.generate_recovery_code()

    def provision_initial_admin(
        self, name: str, login: str, password: str, recovery_code: str
    ) -> dict:
        return self.service.provision_initial_admin(name, login, password, recovery_code)

    def password_recovery_available(self, login: str) -> bool:
        return self.service.password_recovery_available(login)

    def recover_password_with_code(
        self, login: str, recovery_code: str, new_password: str, new_recovery_code: str
    ) -> dict:
        return self.service.recover_password_with_code(login, recovery_code, new_password, new_recovery_code)

    def prepare_own_recovery_code(self) -> str:
        return self.service.prepare_own_recovery_code()

    def configure_own_recovery_code(
        self, current_password: str, recovery_code: str
    ) -> dict:
        return self.service.configure_own_recovery_code(current_password, recovery_code)

    def logout(self) -> None:
        self.service.logout()

    def shutdown(self) -> None:
        self.print_worker.shutdown(timeout=2.0)
        self.service.shutdown()

    def _actor(self, supplied_actor_id: int) -> dict:
        current = self.service.active_user()
        try:
            supplied = int(supplied_actor_id)
        except (TypeError, ValueError) as exc:
            raise ValidationError("Operador inválido.") from exc
        if supplied != int(current["id"]):
            raise AuthorizationError("O operador informado não corresponde à sessão atual.")
        return current

    def _require_admin(self) -> dict:
        current = self.service.active_user()
        if current.get("perfil") != "admin":
            raise AuthorizationError("Esta operação exige um usuário administrador.")
        return current

    # -- atualizações -----------------------------------------------------
    def _update_coordinator(self, *, require_repository: bool) -> UpdateCoordinator:
        policy = UpdatePolicy(
            enabled=bool(getattr(self.settings, "updates_enabled", False)),
            channel=str(getattr(self.settings, "update_channel", "stable")),
            base_url=str(getattr(self.settings, "update_base_url", "")),
            check_interval_hours=int(getattr(self.settings, "update_check_interval_hours", 6)),
        )
        repository = None
        if require_repository:
            if not policy.enabled:
                raise ValidationError("As atualizações online estão desativadas nesta instalação.")
            root_path = Path(self.settings.resource_directory) / "updates" / "trusted" / "root.json"
            if not root_path.is_file():
                raise ValidationError(
                    "A raiz de confiança assinada ainda não foi instalada. Não é seguro habilitar atualizações."
                )
            try:
                bootstrap_root = root_path.read_bytes()
            except OSError as exc:
                raise ValidationError("Não foi possível ler a raiz de confiança da atualização.") from exc
            repository = TufRepository(
                base_url=policy.base_url,
                bootstrap_root=bootstrap_root,
                cache_directory=self.settings.data_directory / "updates" / "tuf",
            )
        return UpdateCoordinator(
            policy=policy,
            state_store=UpdateStateStore(self.settings.update_state_path),
            database_path=self.settings.database_path,
            backup_directory=self.settings.backup_path,
            adapter=VelopackAdapter(),
            repository=repository,
            event_logger=UpdateEventLogger(self.settings.data_directory / "updates" / "events.jsonl"),
        )

    def admin_update_status(self) -> dict[str, Any]:
        self._require_admin()
        coordinator = self._update_coordinator(require_repository=False)
        state = coordinator.state_store.load()
        enabled = bool(getattr(self.settings, "updates_enabled", False))
        root_exists = (Path(self.settings.resource_directory) / "updates" / "trusted" / "root.json").is_file()
        labels = {
            "IDLE": "Sistema atualizado", "AVAILABLE": "Atualização disponível",
            "DOWNLOADING": "Baixando atualização", "DOWNLOADED": "Pronta para instalar",
            "PREPARING": "Preparando cópia de segurança",
            "APPLY_PENDING": "Instalação pendente de reinício",
            "HEALTH_CHECK": "Validando nova versão", "ROLLED_BACK": "Versão anterior restaurada",
            "FAILED": "Atualização requer revisão", "PAUSED": "Atualizações pausadas",
        }
        return {
            "enabled": enabled, "trusted_root_installed": root_exists,
            "channel": getattr(self.settings, "update_channel", "stable"),
            "current_version": state.current_version, "target_version": state.target_version,
            "phase": state.phase.value, "status": labels.get(state.phase.value, state.phase.value),
            "can_apply": state.phase.value == "DOWNLOADED", "configured": enabled and root_exists,
        }

    def admin_check_for_update(self) -> dict[str, Any]:
        self._require_admin()
        coordinator = self._update_coordinator(require_repository=True)
        installation_id = self.service.installation_status().installation_id
        offer = coordinator.check_now(installation_id)
        if offer is None:
            return {"available": False, "message": "Nenhuma atualização liberada para esta instalação."}
        bundle = coordinator.download(offer)
        self._pending_update_offer = offer
        return {
            "available": True, "version": offer.version, "bundle": str(bundle),
            "message": f"Versão {offer.version} autenticada e pronta para instalar.",
        }

    def admin_apply_downloaded_update(self) -> dict[str, Any]:
        self._require_admin()
        offer = self._pending_update_offer
        if offer is None:
            raise ValidationError("Verifique e baixe a atualização antes de instalar.")
        coordinator = self._update_coordinator(require_repository=False)
        state = coordinator.state_store.load()
        if state.target_sequence != offer.sequence or not state.bundle_directory:
            raise ValidationError("O pacote baixado não corresponde à atualização selecionada.")
        coordinator.prepare_apply(
            offer, state.bundle_directory,
            safe_to_apply=lambda: self.service.get_global_open_cash() is None,
        )
        return {"started": True, "message": "Atualização preparada; o TrigoPDV será reiniciado."}

    # -- checkout / products ---------------------------------------------
    def get_open_cash(self, user_id: int) -> dict | None:
        self._actor(user_id)
        return self.service.get_open_cash(user_id)

    def get_global_open_cash(self) -> dict | None:
        return self.service.get_global_open_cash()

    def resume_open_cash(self, cash_id: int, reason: str) -> dict:
        self._require_admin()
        return self.service.resume_open_cash(cash_id, reason)

    def open_cash(self, user_id: int, opening_float: float) -> dict:
        self._actor(user_id)
        return self.service.open_cash(user_id, opening_float)

    def scan_product(self, gtin: str, actor_id: int) -> dict:
        self._actor(actor_id)
        return self.service.scan_product(gtin, actor_id)

    def save_price(self, gtin: str, price: float, actor_id: int) -> dict:
        self._actor(actor_id)
        return self.service.save_price(gtin, price, actor_id)

    def create_product(self, product: Mapping[str, Any], actor_id: int) -> dict:
        self._actor(actor_id)
        return self.service.create_product(dict(product), actor_id)

    def search_products(self, query: str) -> list[dict]:
        return self.service.search_products(query)

    def get_counter_products(self) -> list[dict]:
        return self.service.get_counter_products()

    def admin_approve_and_price(
        self, operator_id: int, gtin: str, price: float, admin_login: str, admin_password: str
    ) -> dict:
        self._actor(operator_id)
        return self.service.admin_approve_and_price(operator_id, gtin, price, admin_login, admin_password)

    def admin_approve_and_create(
        self, operator_id: int, product: Mapping[str, Any], admin_login: str, admin_password: str
    ) -> dict:
        self._actor(operator_id)
        return self.service.admin_approve_and_create(operator_id, dict(product), admin_login, admin_password)

    def get_pix_payload(self, total: float) -> str:
        return self.service.get_pix_payload(total)

    def quote_sale(
        self, operator_id: int, items: Sequence[Mapping[str, Any]]
    ) -> dict[str, Any]:
        self._actor(operator_id)
        return self.service.quote_sale(operator_id, [dict(item) for item in items])

    def authorize_item_cancel(
        self,
        operator_id: int,
        item: Mapping[str, Any],
        *,
        admin_login: str | None = None,
        admin_password: str | None = None,
        admin_user_id: int | None = None,
    ) -> bool:
        actor = self._actor(operator_id)
        if admin_user_id is not None:
            try:
                approved_by = int(admin_user_id)
            except (TypeError, ValueError) as exc:
                raise AuthorizationError("A autorização de administrador é inválida.") from exc
            if approved_by != int(actor["id"]) or actor.get("perfil") != "admin":
                raise AuthorizationError("A autorização de administrador é inválida.")
        return self.service.authorize_item_cancel(
            operator_id,
            dict(item),
            admin_login=admin_login,
            admin_password=admin_password,
            admin_user_id=admin_user_id,
        )

    def cancel_sale(
        self,
        sale_id: int,
        operator_id: int,
        reason: str,
        idempotency_key: str,
        *,
        admin_login: str,
        admin_password: str,
    ) -> dict:
        self._actor(operator_id)
        return self.service.cancel_sale_admin(
            sale_id,
            reason,
            idempotency_key,
            admin_login=admin_login,
            admin_password=admin_password,
        )

    def finalize_sale(
        self,
        cash_id: int,
        operator_id: int,
        items: Sequence[Mapping[str, Any]],
        payment_method: str,
        amount_received: float | None = None,
        *,
        chave_idempotencia: str | None = None,
        manual_authorization: Mapping[str, Any] | None = None,
    ) -> dict:
        operator = self._actor(operator_id)
        sale = self.service.finalize_sale(
            cash_id,
            operator_id,
            [dict(item) for item in items],
            payment_method,
            amount_received,
            chave_idempotencia=chave_idempotencia,
            manual_authorization=(
                None if manual_authorization is None else dict(manual_authorization)
            ),
            receipt_context=(
                {
                    "business_name": self.settings.establishment_name,
                    "business_document": self.settings.establishment_document,
                    "address": self.settings.receipt_header,
                }
                if self.settings.printer_enabled
                else None
            ),
        )
        if sale.get("idempotent_replay"):
            sale["print_warning"] = "Venda já confirmada anteriormente; não foi enviado um segundo comprovante."
            return sale

        if self.settings.printer_enabled:
            job_id = sale.get("print_job_id")
            if job_id is not None:
                self.print_worker.enqueue(int(job_id))
            sale["printed"] = False
            sale["print_pending"] = True
            sale["print_warning"] = "Comprovante salvo na fila de impressão; a venda foi concluída."
        else:
            sale["printed"] = False
            sale["print_pending"] = False
            sale["print_warning"] = (
                "Impressão desativada. Nenhum comprovante automático foi criado."
            )
        return sale

    def queue_second_copy(
        self, sale_id: int, operator_id: int, *, idempotency_key: str
    ) -> dict:
        self._actor(operator_id)
        if not self.settings.printer_enabled:
            raise ValidationError(
                "Ative e teste uma impressora antes de solicitar a segunda via."
            )
        job = self.service.queue_receipt_copy(
            sale_id,
            idempotency_key,
            {
                "business_name": self.settings.establishment_name,
                "business_document": self.settings.establishment_document,
                "address": self.settings.receipt_header,
            },
        )
        if not job.get("idempotent_replay"):
            self.print_worker.enqueue(int(job["id"]))
        return job

    def retry_print(self, job_id: int, operator_id: int) -> dict:
        self._actor(operator_id)
        return self.print_worker.retry(job_id, actor_id=operator_id)

    def _record_print_result(self, result: PrintResult, sale_id: Any, operator_id: int) -> None:
        try:
            self.service.audit.log(
                "COMPROVANTE_IMPRESSO" if result.printed else "COMPROVANTE_PENDENTE",
                "VENDA",
                entity_id=sale_id,
                actor_id=operator_id,
                details={"mensagem": result.message},
            )
        except Exception:
            # Sale completion and operator feedback remain reliable even if audit
            # storage is temporarily unavailable after the committed transaction.
            pass

    # -- cash -------------------------------------------------------------
    def record_cash_movement(
        self,
        cash_id: int,
        operator_id: int,
        movement_type: str,
        amount: float,
        observation: str,
        *,
        chave_idempotencia: str | None = None,
    ) -> dict:
        self._actor(operator_id)
        return self.service.record_cash_movement(
            cash_id,
            operator_id,
            movement_type,
            amount,
            observation,
            chave_idempotencia=chave_idempotencia,
        )

    def close_cash(self, cash_id: int, operator_id: int, counted_cash: float, justification: str) -> dict:
        self._actor(operator_id)
        return self.service.close_cash(cash_id, operator_id, counted_cash, justification)

    def cash_summary(self, cash_id: int, operator_id: int) -> dict:
        """Returns the current cash amount for the permitted visible profile."""

        self._actor(operator_id)
        return self.service.cash_summary(
            cash_id,
            reveal_expected=bool(self.settings.show_expected_to_operator),
        )

    def change_password(self, user_id: int, current_password: str, new_password: str) -> None:
        self.service.change_password(user_id, current_password, new_password)

    def admin_users(self) -> list[dict]:
        self._require_admin()
        return self.service.admin_users()

    def create_user_admin(
        self, nome: str, login: str, senha: str, perfil: str, actor_id: int
    ) -> dict:
        self._actor(actor_id)
        self._require_admin()
        return self.service.create_user_admin(nome, login, senha, perfil, actor_id)

    def reset_user_password_admin(self, user_id: int, temporary_password: str, actor_id: int) -> dict:
        self._actor(actor_id)
        self._require_admin()
        return self.service.reset_user_password_admin(user_id, temporary_password, actor_id)

    # -- administrator workspaces ----------------------------------------
    def admin_dashboard(self) -> dict:
        self._require_admin()
        return self.service.dashboard_summary()

    def admin_products(self, query: str = "") -> list[dict]:
        self._require_admin()
        return self.service.search_products(query, include_inactive=True)

    def save_product_admin(self, product: Mapping[str, Any], actor_id: int) -> dict:
        self._actor(actor_id)
        self._require_admin()
        return self.service.save_product_admin(dict(product), actor_id)

    def admin_cash_closures(self) -> list[dict]:
        self._require_admin()
        rows = self.service.admin_cash_closures()
        for row in rows:
            owner = self.service.auth.get_user(int(row["usuario_id"]), actor_id=int(self._require_admin()["id"]))
            row["operador"] = owner.get("nome", owner.get("login", ""))
        return rows

    def admin_financial_report(self, start_date: str, end_date: str) -> dict:
        self._require_admin()
        return self.service.admin_financial_report(start_date, end_date)

    def admin_audit_logs(self, limit: int = 200) -> list[dict]:
        self._require_admin()
        return self.service.admin_audit_logs(limit)

    def run_maintenance(self, operation: str, actor_id: int) -> dict:
        self._actor(actor_id)
        self._require_admin()
        return self.service.run_maintenance(operation, actor_id)

    def test_printer(self) -> dict:
        """Envia um comprovante de teste sem criar venda ou alterar estoque."""

        self._require_admin()
        if not self.settings.printer_enabled:
            result = PrintResult(
                False,
                "Impressão desativada; selecione e salve uma impressora antes de testar.",
                "Teste de impressão\nNenhuma impressora foi selecionada.",
            )
            return {"printed": result.printed, "message": result.message, "receipt_text": result.receipt_text}
        printer_name = str(self.settings.printer_name or "").strip()
        if str(self.settings.printer_mode or "").upper() == "PADRAO_WINDOWS":
            printer_name = default_printer_name()
            if not printer_name:
                result = PrintResult(
                    False,
                    "O Windows não possui uma impressora padrão disponível. Escolha uma impressora ou atualize a lista.",
                    "Teste de impressão\nNenhuma impressora padrão foi encontrada.",
                )
                return {"printed": result.printed, "message": result.message, "receipt_text": result.receipt_text}
        # Reduz a janela entre a guarda e o spooler sem prometer serialização
        # entre SQLite e o efeito externo.
        self._require_admin()
        result = self.printer.print_receipt(
            {
                "business_name": self.settings.establishment_name,
                "business_document": self.settings.establishment_document,
                "address": self.settings.receipt_header,
                "sale_id": "TESTE",
                "date": datetime.now().strftime("%d/%m/%Y %H:%M"),
                "test_print": True,
                "printer_name": printer_name,
                "items": [],
                "total": 0,
                "payment_method": "—",
                "operator": "Administrador",
            }
        )
        return {"printed": result.printed, "message": result.message, "receipt_text": result.receipt_text}
