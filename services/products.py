"""Catálogo, preço, estoque, validade e fluxo de GTIN do caixa."""

from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime, timedelta
import unicodedata
from typing import Any, Optional

from db.database import Database
from integrations.cosmos import CosmosClient
from integrations.open_food_facts import OpenFoodFactsClient, normalize_gtin

from .audit import AuditService, now
from .errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from .money import as_float, money, signed_quantity, stock
from .security import get_active_user, require_admin
from .text import normalize_display_text


COUNTER_CODE = re.compile(r"^[A-Za-z0-9_.-]{2,50}$")
CATEGORIES = (
    "Padaria", "Pães", "Salgados", "Doces", "Bolos", "Lanches", "Bebidas",
    "Refrigerantes", "Sucos", "Águas", "Cafés", "Laticínios", "Frios",
    "Biscoitos", "Chocolates", "Conveniência", "Ingredientes", "Congelados", "Outros",
)


def _category(value: Any) -> str:
    """Normaliza categorias para uma lista pequena e consistente no catálogo."""

    raw = normalize_display_text(str(value or "").replace("_", " "))
    if not raw:
        return "Outros"
    folded = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode().casefold()
    aliases = {
        "paes": "Pães", "pao": "Pães", "padaria": "Padaria", "bakery": "Padaria",
        "bebida": "Bebidas", "beverages": "Bebidas", "refrigerante": "Refrigerantes",
        "sucos": "Sucos", "suco": "Sucos", "aguas": "Águas", "agua": "Águas",
        "cafe": "Cafés", "cafes": "Cafés", "laticinio": "Laticínios", "laticinios": "Laticínios",
        "frios": "Frios", "biscoitos": "Biscoitos", "biscoito": "Biscoitos",
        "chocolate": "Chocolates", "chocolates": "Chocolates", "conveniencia": "Conveniência",
        "ingredientes": "Ingredientes", "congelados": "Congelados", "salgados": "Salgados",
        "doces": "Doces", "bolos": "Bolos", "lanches": "Lanches",
    }
    return aliases.get(folded, raw if raw in CATEGORIES else "Outros")


def _short_text(value: Any, field: str, limit: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValidationError(f"O campo {field} é inválido.")
    return normalize_display_text(value, limit)


def normalize_product_code(value: Any) -> str:
    """Aceita GTIN e códigos internos de itens de balcão, sem texto arbitrário."""

    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise ValidationError("Informe o código do produto.")
    raw = str(value).strip()
    compact = raw.replace(" ", "").replace("-", "")
    if compact.isdigit():
        return normalize_gtin(compact)
    if not COUNTER_CODE.fullmatch(raw):
        raise ValidationError("Código do produto inválido.")
    return raw.upper()


def _name(value: Any, field: str = "nome") -> str:
    if not isinstance(value, str):
        raise ValidationError(f"Informe o {field} do produto.")
    result = normalize_display_text(value)
    if not result or len(result) > 180:
        raise ValidationError(f"O {field} deve ter entre 1 e 180 caracteres.")
    return result


def _brand(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValidationError("A marca do produto é inválida.")
    return normalize_display_text(value, 120)


def _unit(value: Any) -> str:
    candidate = str(value or "UN").strip().upper()
    aliases = {"UNIDADE": "UN", "UNITARIO": "UN", "KG": "KG", "KILO": "KG", "QUILO": "KG"}
    candidate = aliases.get(candidate, candidate)
    if candidate not in {"UN", "KG"}:
        raise ValidationError("A unidade deve ser UN ou KG.")
    return candidate


def _date(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()).isoformat()
        except ValueError as exc:
            raise ValidationError("A validade deve estar no formato AAAA-MM-DD.") from exc
    raise ValidationError("A validade do produto é inválida.")


def _product_public(row: dict) -> dict:
    return {
        "gtin": row["gtin"],
        "nome": row["nome"],
        "marca": row["marca"] or "",
        "preco": round(float(row["preco"]), 2),
        "estoque": float(row["estoque"]),
        "data_validade": row["data_validade"],
        "unidade": row["unidade"],
        "estoque_controlado": bool(row["estoque_controlado"]),
        "item_balcao": bool(row["item_balcao"]),
        "ativo": bool(row["ativo"]),
        "origem": row["origem"],
        "tipo_codigo": row.get("tipo_codigo") or ("GTIN" if str(row["gtin"]).isdigit() else "PLU"),
        "categoria": row.get("categoria") or "Outros",
        "subcategoria": row.get("subcategoria") or "",
        "detalhes_embalagem": row.get("detalhes_embalagem") or "",
        "validacao_codigo": row.get("validacao_codigo") or "PENDENTE",
        "fonte_validacao": row.get("fonte_validacao") or "",
        "validado_em": row.get("validado_em"),
        "criado_em": row.get("criado_em"),
        "atualizado_em": row.get("atualizado_em"),
    }


class ProductService:
    def __init__(
        self,
        database: Database,
        *,
        external_client: Optional[OpenFoodFactsClient] = None,
        cosmos_client: Optional[CosmosClient] = None,
        audit: Optional[AuditService] = None,
    ):
        self.database = database
        self.external_client = external_client or OpenFoodFactsClient()
        self.cosmos_client = cosmos_client
        self.audit = audit or AuditService(database)

    def find_by_gtin(self, gtin: str, *, include_inactive: bool = False) -> Optional[dict]:
        code = normalize_product_code(gtin)
        with self.database.transaction() as connection:
            sql = "SELECT * FROM produtos WHERE gtin = ?"
            if not include_inactive:
                sql += " AND ativo = 1"
            row = connection.execute(sql, (code,)).fetchone()
            return _product_public(dict(row)) if row else None

    def get_product(self, gtin: str, *, include_inactive: bool = False) -> dict:
        product = self.find_by_gtin(gtin, include_inactive=include_inactive)
        if product is None:
            raise NotFoundError("Produto não encontrado.")
        return product

    def _cached_lookup(self, code: str) -> Optional[dict]:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM cache_gtin WHERE gtin = ?", (code,)).fetchone()
        if row is None:
            return None
        try:
            if datetime.fromisoformat(str(row["expira_em"])) <= datetime.now().astimezone():
                return None
        except (TypeError, ValueError):
            return None
        return dict(row)

    def _cache_result(
        self,
        code: str,
        *,
        status: str,
        source: str,
        ttl_seconds: int,
        name: str = "",
        brand: str = "",
        category: str = "Outros",
        packaging: str = "",
    ) -> None:
        expires = (datetime.now().astimezone() + timedelta(seconds=ttl_seconds)).isoformat(timespec="seconds")
        with self.database.transaction(write=True) as connection:
            connection.execute(
                "INSERT INTO cache_gtin(gtin, status, fonte, nome, marca, categoria, detalhes_embalagem, consultado_em, expira_em, tentativas) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1) ON CONFLICT(gtin) DO UPDATE SET "
                "status=excluded.status, fonte=excluded.fonte, nome=excluded.nome, marca=excluded.marca, "
                "categoria=excluded.categoria, detalhes_embalagem=excluded.detalhes_embalagem, "
                "consultado_em=excluded.consultado_em, expira_em=excluded.expira_em, tentativas=cache_gtin.tentativas + 1",
                (code, status, source, name or None, brand or None, _category(category), packaging or None, now(), expires),
            )

    def lookup_external(self, gtin: str, *, actor_id: int) -> dict:
        """Implementa leitor → catálogo/cache local → fontes externas → cadastro manual."""

        code = normalize_product_code(gtin)
        # Mesmo consulta com cache é vinculada a um operador para manter rastro.
        with self.database.transaction() as connection:
            get_active_user(connection, actor_id)
        local = self.find_by_gtin(code, include_inactive=True)
        if local is not None:
            if not local["ativo"]:
                return {"status": "INACTIVE", "product": local, "message": "Este produto está inativo."}
            return {
                "status": "FOUND" if local["preco"] > 0 else "PRICE_REQUIRED",
                "product": local,
                "source": "local",
                "message": "" if local["preco"] > 0 else "Informe o preço do produto.",
            }
        if not code.isdigit():
            return {
                "status": "MANUAL_ENTRY_REQUIRED",
                "product": None,
                "source": "local",
                "message": "Código interno não cadastrado. Cadastre o item para continuar.",
            }
        cached = self._cached_lookup(code)
        if cached is not None:
            if cached["status"] == "NAO_ENCONTRADO":
                return {
                    "status": "MANUAL_ENTRY_REQUIRED", "product": None, "source": cached["fonte"],
                    "message": "Este código não foi encontrado na última consulta. Cadastre manualmente ou tente novamente mais tarde.",
                }
            if cached["status"] == "INDISPONIVEL":
                return {
                    "status": "OFFLINE", "product": None, "source": cached["fonte"],
                    "message": "A consulta externa está temporariamente indisponível. Cadastre manualmente e continue a venda.",
                }
        external = None
        source = "open_food_facts"
        failures: list[Exception] = []
        try:
            external = self.external_client.lookup(code)
        except Exception as exc:
            failures.append(exc)
        if external is None and self.cosmos_client is not None and self.cosmos_client.enabled:
            try:
                external = self.cosmos_client.lookup(code)
                if external is not None:
                    source = "cosmos"
            except Exception as exc:
                failures.append(exc)
        if external is None and failures:
            # Falhas das fontes externas não podem paralisar o caixa. O
            # detalhe técnico não é registrado, para não expor dados locais.
            with self.database.transaction(write=True) as connection:
                AuditService.record(
                    connection,
                    "GTIN_CONSULTA_INDISPONIVEL",
                    "PRODUTO",
                    entity_id=code,
                    actor_id=actor_id,
                    details={"fontes": ["open_food_facts", "cosmos"] if self.cosmos_client and self.cosmos_client.enabled else ["open_food_facts"]},
                )
            # Falha de rede é registrada para diagnóstico, mas não bloqueia a
            # próxima leitura: ao reconectar, a tentativa deve acontecer de
            # imediato sem exigir reinício do programa.
            self._cache_result(code, status="INDISPONIVEL", source="externo", ttl_seconds=0)
            return {
                "status": "OFFLINE",
                "product": None,
                "source": "offline",
                "message": str(failures[-1]) or "Não foi possível consultar o produto. Faça o cadastro manual.",
            }
        if external is None:
            self._cache_result(code, status="NAO_ENCONTRADO", source="externo", ttl_seconds=86400)
            return {
                "status": "MANUAL_ENTRY_REQUIRED",
                "product": None,
                "source": "not_found",
                "message": "Produto não encontrado. Faça o cadastro manual.",
            }
        external_name = _name(normalize_display_text(getattr(external, "name", "")))
        external_brand = _brand(normalize_display_text(getattr(external, "brand", "")))
        external_category = _category(getattr(external, "category", "Outros"))
        external_packaging = _short_text(
            normalize_display_text(getattr(external, "packaging", "")), "embalagem", 120
        )
        with self.database.transaction(write=True) as connection:
            # Dois leitores podem consultar o mesmo GTIN. INSERT OR IGNORE mantém
            # o cache local consistente sem apagar um preço recém-definido.
            connection.execute(
                "INSERT OR IGNORE INTO produtos(gtin, nome, marca, preco, estoque, data_validade, unidade, "
                "estoque_controlado, item_balcao, ativo, origem, tipo_codigo, categoria, detalhes_embalagem, "
                "validacao_codigo, fonte_validacao, validado_em, criado_em, atualizado_em) "
                # A API fornece identificação, não o saldo físico da loja. O
                # produto entra sem controle de estoque até o administrador
                # configurar um saldo, permitindo a precificação rápida e a
                # primeira venda sem aceitar estoque negativo em itens já
                # controlados.
                # O schema legado identifica todo item de catálogo externo
                # como open_food_facts. A fonte exata fica no retorno e na
                # auditoria, sem exigir migração destrutiva na base local.
                "VALUES (?, ?, ?, 0, 0, NULL, 'UN', 0, 0, 1, 'open_food_facts', 'GTIN', ?, ?, 'CONFIRMADO', ?, ?, ?, ?)",
                (code, external_name, external_brand, external_category,
                 external_packaging, source, now(), now(), now()),
            )
            row = connection.execute("SELECT * FROM produtos WHERE gtin = ?", (code,)).fetchone()
            AuditService.record(
                connection,
                "GTIN_CONSULTADO_EXTERNAMENTE",
                "PRODUTO",
                entity_id=code,
                actor_id=actor_id,
                details={"fonte": source, "encontrado": True},
            )
            product = _product_public(dict(row))
        self._cache_result(
            code,
            status="ENCONTRADO",
            source=source,
            ttl_seconds=604800,
            name=external_name,
            brand=external_brand,
            category=external_category,
            packaging=external_packaging,
        )
        return {
            "status": "PRICE_REQUIRED",
            "product": product,
            "source": source,
            "message": "Produto incluído no cache. Informe o preço antes de vender.",
        }

    def create_product(
        self,
        gtin: str,
        nome: str,
        preco: Any = 0,
        *,
        marca: str = "",
        estoque: Any = 0,
        data_validade: Any = None,
        unidade: str = "UN",
        estoque_controlado: bool = True,
        item_balcao: bool = False,
        ativo: bool = True,
        categoria: str = "Outros",
        subcategoria: str = "",
        detalhes_embalagem: str = "",
        tipo_codigo: str | None = None,
        validacao_codigo: str | None = None,
        fonte_validacao: str = "",
        actor_id: int,
    ) -> dict:
        code = normalize_product_code(gtin)
        product_name = _name(nome)
        product_brand = _brand(marca)
        price = money(preco, "preço")
        stock_amount = stock(estoque, "estoque")
        expiry = _date(data_validade)
        unit = _unit(unidade)
        code_type = (str(tipo_codigo or ("GTIN" if code.isdigit() else "PLU")).strip().upper())
        if code_type not in {"GTIN", "PLU"}:
            raise ValidationError("O tipo do código deve ser GTIN ou PLU.")
        normalized_category = _category(categoria)
        normalized_subcategory = _short_text(subcategoria, "subcategoria", 100)
        normalized_packaging = _short_text(detalhes_embalagem, "embalagem", 120)
        validation = str(validacao_codigo or ("VALIDO_INTERNO" if code_type == "PLU" else "PENDENTE")).strip().upper()
        if validation not in {"PENDENTE", "VALIDO_ESTRUTURAL", "CONFIRMADO", "INCOMPATIVEL", "VALIDO_INTERNO"}:
            raise ValidationError("O estado de validação do código é inválido.")
        if not isinstance(estoque_controlado, bool) or not isinstance(item_balcao, bool) or not isinstance(ativo, bool):
            raise ValidationError("Os indicadores do produto são inválidos.")
        with self.database.transaction(write=True) as connection:
            require_admin(connection, actor_id)
            try:
                connection.execute(
                    "INSERT INTO produtos(gtin, nome, marca, preco, estoque, data_validade, unidade, estoque_controlado, "
                    "item_balcao, ativo, origem, tipo_codigo, categoria, subcategoria, detalhes_embalagem, "
                    "validacao_codigo, fonte_validacao, validado_em, criado_em, atualizado_em) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'manual', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (code, product_name, product_brand, as_float(price), as_float(stock_amount), expiry, unit,
                     int(estoque_controlado), int(item_balcao), int(ativo), code_type, normalized_category,
                     normalized_subcategory, normalized_packaging, validation, _short_text(fonte_validacao, "fonte", 80),
                     now() if validation in {"CONFIRMADO", "VALIDO_ESTRUTURAL", "VALIDO_INTERNO"} else None, now(), now()),
                )
            except sqlite3.IntegrityError as exc:
                raise ConflictError("Já existe um produto com este código.") from exc
            row = connection.execute("SELECT * FROM produtos WHERE gtin = ?", (code,)).fetchone()
            AuditService.record(
                connection,
                "PRODUTO_CRIADO",
                "PRODUTO",
                entity_id=code,
                actor_id=actor_id,
                details={"nome": product_name, "preco": as_float(price)},
            )
            return _product_public(dict(row))

    def update_product(self, gtin: str, *, actor_id: int, **changes: Any) -> dict:
        """Atualiza somente campos explicitamente recebidos e registra antes/depois."""

        code = normalize_product_code(gtin)
        allowed = {
            "nome", "marca", "preco", "estoque", "data_validade", "unidade", "estoque_controlado", "item_balcao", "ativo",
            "categoria", "subcategoria", "detalhes_embalagem", "validacao_codigo", "fonte_validacao",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise ValidationError(f"Campo de produto não permitido: {sorted(unknown)[0]}.")
        if not changes:
            return self.get_product(code, include_inactive=True)
        normalized: dict[str, Any] = {}
        for key, value in changes.items():
            if key == "nome":
                normalized[key] = _name(value)
            elif key == "marca":
                normalized[key] = _brand(value)
            elif key in {"preco", "estoque"}:
                normalized[key] = as_float(money(value, "preço")) if key == "preco" else as_float(stock(value, "estoque"))
            elif key == "data_validade":
                normalized[key] = _date(value)
            elif key == "unidade":
                normalized[key] = _unit(value)
            elif key == "categoria":
                normalized[key] = _category(value)
            elif key == "subcategoria":
                normalized[key] = _short_text(value, "subcategoria", 100)
            elif key == "detalhes_embalagem":
                normalized[key] = _short_text(value, "embalagem", 120)
            elif key == "validacao_codigo":
                candidate = str(value or "").strip().upper()
                if candidate not in {"PENDENTE", "VALIDO_ESTRUTURAL", "CONFIRMADO", "INCOMPATIVEL", "VALIDO_INTERNO"}:
                    raise ValidationError("O estado de validação do código é inválido.")
                normalized[key] = candidate
                normalized["validado_em"] = now() if candidate in {"CONFIRMADO", "VALIDO_ESTRUTURAL", "VALIDO_INTERNO"} else None
            elif key == "fonte_validacao":
                normalized[key] = _short_text(value, "fonte", 80)
            elif key in {"estoque_controlado", "item_balcao", "ativo"}:
                if not isinstance(value, bool):
                    raise ValidationError(f"O campo {key} deve ser verdadeiro ou falso.")
                normalized[key] = int(value)
        with self.database.transaction(write=True) as connection:
            require_admin(connection, actor_id)
            before_row = connection.execute("SELECT * FROM produtos WHERE gtin = ?", (code,)).fetchone()
            if before_row is None:
                raise NotFoundError("Produto não encontrado.")
            before = _product_public(dict(before_row))
            assignments = ", ".join(f"{field} = ?" for field in normalized) + ", atualizado_em = ?"
            connection.execute(
                f"UPDATE produtos SET {assignments} WHERE gtin = ?", [*normalized.values(), now(), code]
            )
            after_row = connection.execute("SELECT * FROM produtos WHERE gtin = ?", (code,)).fetchone()
            after = _product_public(dict(after_row))
            AuditService.record(
                connection,
                "PRODUTO_ATUALIZADO",
                "PRODUTO",
                entity_id=code,
                actor_id=actor_id,
                details={"antes": {key: before[key] for key in normalized}, "depois": {key: after[key] for key in normalized}},
            )
            return after

    def set_price(self, gtin: str, preco: Any, *, actor_id: int) -> dict:
        return self.update_product(gtin, actor_id=actor_id, preco=preco)

    def adjust_stock(self, gtin: str, delta: Any, *, actor_id: int, observacao: str = "") -> dict:
        code = normalize_product_code(gtin)
        # estoque pode receber frações; permite delta negativo, mas nunca saldo negativo.
        signed = as_float(signed_quantity(delta, "ajuste de estoque"))
        note = " ".join(str(observacao or "").split())[:250]
        with self.database.transaction(write=True) as connection:
            require_admin(connection, actor_id)
            row = connection.execute("SELECT * FROM produtos WHERE gtin = ?", (code,)).fetchone()
            if row is None:
                raise NotFoundError("Produto não encontrado.")
            product = dict(row)
            new_stock = round(float(product["estoque"]) + signed, 3)
            if new_stock < 0:
                raise ConflictError("O ajuste deixaria o estoque negativo.")
            connection.execute("UPDATE produtos SET estoque = ?, atualizado_em = ? WHERE gtin = ?", (new_stock, now(), code))
            updated = _product_public(dict(connection.execute("SELECT * FROM produtos WHERE gtin = ?", (code,)).fetchone()))
            AuditService.record(
                connection,
                "ESTOQUE_AJUSTADO",
                "PRODUTO",
                entity_id=code,
                actor_id=actor_id,
                details={"delta": signed, "estoque_anterior": product["estoque"], "estoque_novo": new_stock, "observacao": note},
            )
            return updated

    def set_active(self, gtin: str, ativo: bool, *, actor_id: int) -> dict:
        return self.update_product(gtin, actor_id=actor_id, ativo=ativo)

    def delete_product(self, gtin: str, *, actor_id: int) -> None:
        """Preserva histórico de vendas: produto vendido é apenas inativado."""

        code = normalize_product_code(gtin)
        with self.database.transaction(write=True) as connection:
            require_admin(connection, actor_id)
            row = connection.execute("SELECT gtin FROM produtos WHERE gtin = ?", (code,)).fetchone()
            if row is None:
                raise NotFoundError("Produto não encontrado.")
            used = connection.execute("SELECT 1 FROM itens_venda WHERE gtin = ? LIMIT 1", (code,)).fetchone()
            if used:
                connection.execute("UPDATE produtos SET ativo = 0, atualizado_em = ? WHERE gtin = ?", (now(), code))
                action = "PRODUTO_INATIVADO"
            else:
                connection.execute("DELETE FROM produtos WHERE gtin = ?", (code,))
                action = "PRODUTO_EXCLUIDO"
            AuditService.record(connection, action, "PRODUTO", entity_id=code, actor_id=actor_id)

    def search(self, query: str = "", *, include_inactive: bool = False, limit: int = 100) -> list[dict]:
        try:
            safe_limit = max(1, min(int(limit), 500))
        except (TypeError, ValueError) as exc:
            raise ValidationError("Limite de busca inválido.") from exc
        text = " ".join(str(query or "").split())[:180]
        clauses = [] if include_inactive else ["ativo = 1"]
        parameters: list[Any] = []
        if text:
            clauses.append("(nome LIKE ? ESCAPE '\\' COLLATE NOCASE OR gtin LIKE ? ESCAPE '\\' COLLATE NOCASE OR marca LIKE ? ESCAPE '\\' COLLATE NOCASE OR categoria LIKE ? ESCAPE '\\' COLLATE NOCASE OR subcategoria LIKE ? ESCAPE '\\' COLLATE NOCASE)")
            escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            parameters.extend([f"%{escaped}%"] * 5)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        order_parameters: list[Any] = []
        if text:
            prefix = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            order_sql = (
                "CASE WHEN gtin = ? THEN 0 "
                "WHEN gtin LIKE ? ESCAPE '\\' COLLATE NOCASE THEN 1 "
                "WHEN nome = ? COLLATE NOCASE THEN 2 "
                "WHEN nome LIKE ? ESCAPE '\\' COLLATE NOCASE THEN 3 "
                "WHEN marca = ? COLLATE NOCASE THEN 4 ELSE 5 END, "
            )
            order_parameters.extend([text, f"{prefix}%", text, f"{prefix}%", text])
        else:
            order_sql = ""
        parameters.extend(order_parameters)
        parameters.append(safe_limit)
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM produtos" + where + " ORDER BY " + order_sql + "nome COLLATE NOCASE, gtin LIMIT ?", parameters
            ).fetchall()
            return [_product_public(dict(row)) for row in rows]

    def list_counter_products(self) -> list[dict]:
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM produtos WHERE ativo = 1 AND item_balcao = 1 AND preco > 0 ORDER BY nome COLLATE NOCASE"
            ).fetchall()
            return [_product_public(dict(row)) for row in rows]

    def expiring_products(self, *, days: int = 7) -> list[dict]:
        try:
            horizon_days = int(days)
        except (TypeError, ValueError) as exc:
            raise ValidationError("Prazo de validade inválido.") from exc
        if horizon_days < 0 or horizon_days > 365:
            raise ValidationError("Prazo de validade deve estar entre 0 e 365 dias.")
        today = date.today()
        horizon = today + timedelta(days=horizon_days)
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM produtos WHERE ativo = 1 AND data_validade IS NOT NULL "
                "AND data_validade <= ? ORDER BY data_validade, nome COLLATE NOCASE",
                (horizon.isoformat(),),
            ).fetchall()
            result = []
            for row in rows:
                product = _product_public(dict(row))
                expiry = date.fromisoformat(product["data_validade"])
                product["dias_para_vencer"] = (expiry - today).days
                product["vencido"] = expiry < today
                result.append(product)
            return result
