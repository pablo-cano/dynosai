# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Deterministic greenfield and brownfield fixtures used by the diagnostic harness."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


def brownfield_supportdesk_spec() -> dict[str, Any]:
    return {
        "objective": "Añadir escalado prioritario para tickets críticos de clientes premium sin alterar los SLA existentes del resto de tickets.",
        "scope": [
            "Cálculo del SLA al crear tickets",
            "Marcado de escalado requerido en el modelo de ticket",
            "Respuesta de la API de creación de tickets",
            "Pruebas de regresión del comportamiento existente",
        ],
        "out_of_scope": [
            "Enviar notificaciones automáticas",
            "Persistencia externa o migraciones de base de datos",
            "Cambiar los SLA de clientes standard o severidades no críticas",
        ],
        "requirements": [
            {"id":"REQ-001","text":"Un ticket de severidad critical para un cliente premium debe tener un SLA de 60 minutos.","priority":"must"},
            {"id":"REQ-002","text":"Un ticket critical de cliente premium debe quedar marcado con escalation_required igual a true.","priority":"must"},
            {"id":"REQ-003","text":"Los tickets critical de clientes standard deben conservar el SLA existente de 240 minutos y no requerir escalado.","priority":"must"},
            {"id":"REQ-004","text":"Las severidades high y normal deben conservar sus SLA actuales independientemente de que el cliente sea premium.","priority":"must"},
            {"id":"REQ-005","text":"La respuesta de creación de tickets debe exponer sla_minutes y escalation_required junto con los campos existentes.","priority":"must"},
            {"id":"REQ-006","text":"La funcionalidad no debe disparar notificaciones automáticas ni introducir efectos laterales fuera del flujo actual de creación.","priority":"should"},
        ],
        "acceptance_criteria": [
            {"id":"AC-001","requirement":"REQ-001","text":"Dado un cliente premium, cuando crea un ticket critical, entonces el ticket resultante tiene sla_minutes igual a 60."},
            {"id":"AC-002","requirement":"REQ-002","text":"Dado un cliente premium, cuando crea un ticket critical, entonces escalation_required es true."},
            {"id":"AC-003","requirement":"REQ-003","text":"Dado un cliente standard, cuando crea un ticket critical, entonces sla_minutes sigue siendo 240 y escalation_required es false."},
            {"id":"AC-004","requirement":"REQ-004","text":"Dado un cliente premium, cuando crea un ticket high, entonces conserva sla_minutes igual a 480 y escalation_required es false."},
            {"id":"AC-005","requirement":"REQ-004","text":"Dado cualquier cliente, cuando crea un ticket normal, entonces conserva sla_minutes igual a 1440 y no requiere escalado."},
            {"id":"AC-006","requirement":"REQ-005","text":"Cuando la API crea un ticket premium critical, la respuesta contiene los campos existentes y además sla_minutes=60 y escalation_required=true."},
            {"id":"AC-007","requirement":"REQ-006","text":"Cuando se crea un ticket premium critical, no se invoca ningún servicio de notificaciones y solo se persiste el ticket mediante el repositorio existente."},
        ],
        "business_rules": [
            {"id":"RULE-001","text":"Solo la combinación customer.tier=premium y severity=critical activa el SLA prioritario y el escalado."},
            {"id":"RULE-002","text":"Los SLA actuales son critical=240, high=480 y normal=1440 salvo la excepción premium+critical."},
        ],
        "unknowns": [],
        "affected_features": [],
    }


def brownfield_supportdesk_plan() -> dict[str, Any]:
    return {
        "approach": "Extender la política de SLA existente para considerar el tier del cliente, propagar el indicador de escalado por el modelo y servicio actuales, y exponerlo en el payload API sin añadir dependencias ni efectos laterales.",
        "architecture_delta": [
            "Mantener la lógica de SLA en supportdesk/services/sla.py y convertirla en una política dependiente de customer tier y severity.",
            "Añadir escalation_required al modelo Ticket con valor explícito generado por TicketService.",
            "Conservar InMemoryTicketRepository y el contrato de creación existentes.",
        ],
        "files": [
            {"path":"supportdesk/domain/ticket.py","action":"modify","role":"source","reason":"El ticket debe transportar escalation_required."},
            {"path":"supportdesk/services/sla.py","action":"modify","role":"source","reason":"La política actual contiene los SLA y debe modelar la excepción premium+critical."},
            {"path":"supportdesk/services/ticket_service.py","action":"modify","role":"source","reason":"La creación del ticket debe aplicar la política con el cliente existente."},
            {"path":"supportdesk/api/tickets.py","action":"modify","role":"source","reason":"El payload debe exponer escalation_required preservando campos existentes."},
            {"path":"tests/test_sla.py","action":"modify","role":"test","reason":"Cubrir la excepción premium y la regresión de SLA standard/high/normal."},
            {"path":"tests/test_ticket_service.py","action":"modify","role":"test","reason":"Verificar propagación del SLA y escalado al modelo persistido."},
            {"path":"tests/test_ticket_api.py","action":"modify","role":"test","reason":"Verificar el contrato observable de la API."},
        ],
        "risks": [
            "Cambiar accidentalmente los SLA existentes para clientes standard.",
            "Acoplar la API a lógica de SLA que debe permanecer en servicios.",
            "Introducir un side effect de notificación no solicitado.",
        ],
        "validation_profiles": ["unit"],
        "tasks": [
            {
                "title":"Extender la política de SLA premium critical",
                "description":"Modificar la política de SLA existente para devolver 60 minutos y escalado únicamente para premium+critical, manteniendo los valores actuales para el resto.",
                "requirements":["REQ-001","REQ-003","REQ-004"],
                "acceptance":["AC-001","AC-003","AC-004","AC-005"],
                "files":["supportdesk/services/sla.py","tests/test_sla.py"],
                "depends_on":[],
                "evidence_required":["git_diff","test_result"],
            },
            {
                "title":"Propagar el escalado por dominio y servicio",
                "description":"Añadir escalation_required al Ticket y hacer que TicketService aplique la política de SLA al crear y persistir el ticket sin introducir notificaciones.",
                "requirements":["REQ-002","REQ-006"],
                "acceptance":["AC-002","AC-007"],
                "files":["supportdesk/domain/ticket.py","supportdesk/services/ticket_service.py","tests/test_ticket_service.py"],
                "depends_on":[0],
                "evidence_required":["git_diff","test_result"],
            },
            {
                "title":"Exponer escalado en la API y cerrar regresión",
                "description":"Incluir escalation_required en el payload de creación conservando todos los campos existentes y cubrir el contrato API con tests de regresión.",
                "requirements":["REQ-005"],
                "acceptance":["AC-006"],
                "files":["supportdesk/api/tickets.py","tests/test_ticket_api.py"],
                "depends_on":[1],
                "evidence_required":["git_diff","test_result"],
            },
        ],
    }


def seed_supportdesk_brownfield(root: Path) -> dict[str, Any]:
    """Create a deterministic non-trivial existing repository for brownfield E2E."""
    root.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {
        ".gitignore": "__pycache__/\n*.py[cod]\n.dynosai/\n",
        "README.md": "# SupportDesk\n\nLegacy-style local Python helpdesk used for DynosAI brownfield validation.\n",
        "supportdesk/__init__.py": "\"\"\"SupportDesk application package.\"\"\"\n",
        "supportdesk/domain/__init__.py": "",
        "supportdesk/domain/customer.py": '''from dataclasses import dataclass\n\n@dataclass(frozen=True)\nclass Customer:\n    \"\"\"Customer account. tier is either standard or premium.\"\"\"\n    id: str\n    tier: str = "standard"\n\n    def is_premium(self) -> bool:\n        return self.tier == "premium"\n''',
        "supportdesk/domain/ticket.py": '''from dataclasses import dataclass\n\n@dataclass(frozen=True)\nclass Ticket:\n    \"\"\"Persisted support ticket with the SLA chosen at creation time.\"\"\"\n    id: str\n    customer_id: str\n    severity: str\n    summary: str\n    sla_minutes: int\n''',
        "supportdesk/services/__init__.py": "",
        "supportdesk/services/sla.py": '''SLA_MINUTES = {"critical": 240, "high": 480, "normal": 1440}\n\ndef sla_minutes_for(severity: str) -> int:\n    \"\"\"Return the current SLA in minutes for a ticket severity.\"\"\"\n    try:\n        return SLA_MINUTES[severity]\n    except KeyError as exc:\n        raise ValueError(f"unsupported severity: {severity}") from exc\n''',
        "supportdesk/services/ticket_service.py": '''from supportdesk.domain.customer import Customer\nfrom supportdesk.domain.ticket import Ticket\nfrom supportdesk.services.sla import sla_minutes_for\n\nclass TicketService:\n    \"\"\"Create tickets using the existing severity-based SLA policy.\"\"\"\n    def __init__(self, repository):\n        self.repository = repository\n\n    def create_ticket(self, customer: Customer, severity: str, summary: str) -> Ticket:\n        if not summary.strip():\n            raise ValueError("summary is required")\n        ticket = Ticket(\n            id=self.repository.next_id(),\n            customer_id=customer.id,\n            severity=severity,\n            summary=summary.strip(),\n            sla_minutes=sla_minutes_for(severity),\n        )\n        self.repository.save(ticket)\n        return ticket\n''',
        "supportdesk/repositories/__init__.py": "",
        "supportdesk/repositories/tickets.py": '''class InMemoryTicketRepository:\n    \"\"\"Small repository used by the application and unit tests.\"\"\"\n    def __init__(self):\n        self.items = []\n\n    def next_id(self) -> str:\n        return f"T-{len(self.items)+1:04d}"\n\n    def save(self, ticket) -> None:\n        self.items.append(ticket)\n\n    def get(self, ticket_id: str):\n        return next((item for item in self.items if item.id == ticket_id), None)\n''',
        "supportdesk/api/__init__.py": "",
        "supportdesk/api/tickets.py": '''def create_ticket_payload(service, customer, severity: str, summary: str) -> dict:\n    \"\"\"Application API adapter for ticket creation.\"\"\"\n    ticket = service.create_ticket(customer, severity, summary)\n    return {\n        "id": ticket.id,\n        "customer_id": ticket.customer_id,\n        "severity": ticket.severity,\n        "summary": ticket.summary,\n        "sla_minutes": ticket.sla_minutes,\n    }\n''',
        "supportdesk/notifications/__init__.py": "",
        "supportdesk/notifications/service.py": '''class NotificationService:\n    def __init__(self):\n        self.sent = []\n\n    def send(self, destination: str, message: str) -> None:\n        self.sent.append((destination, message))\n''',
        "supportdesk/auth/__init__.py": "",
        "supportdesk/auth/permissions.py": '''def can_view_ticket(role: str) -> bool:\n    return role in {"agent", "manager", "admin"}\n\ndef can_close_ticket(role: str) -> bool:\n    return role in {"manager", "admin"}\n''',
        "supportdesk/catalog/__init__.py": "",
        "supportdesk/catalog/categories.py": '''CATEGORIES = {"billing", "technical", "contract", "general"}\n\ndef normalize_category(value: str) -> str:\n    value = value.strip().lower()\n    return value if value in CATEGORIES else "general"\n''',
        "supportdesk/reporting/__init__.py": "",
        "supportdesk/reporting/metrics.py": '''def resolution_rate(closed: int, total: int) -> float:\n    return 0.0 if total == 0 else closed / total\n\ndef average_minutes(values: list[int]) -> float:\n    return 0.0 if not values else sum(values) / len(values)\n''',
        "supportdesk/billing/__init__.py": "",
        "supportdesk/billing/invoice.py": '''def format_invoice_reference(customer_id: str, sequence: int) -> str:\n    return f"INV-{customer_id}-{sequence:05d}"\n''',
        "tests/__init__.py": "",
        "tests/test_sla.py": '''import unittest\nfrom supportdesk.services.sla import sla_minutes_for\n\nclass SlaTests(unittest.TestCase):\n    def test_current_sla_values(self):\n        self.assertEqual(sla_minutes_for("critical"), 240)\n        self.assertEqual(sla_minutes_for("high"), 480)\n        self.assertEqual(sla_minutes_for("normal"), 1440)\n\n    def test_unknown_severity_is_rejected(self):\n        with self.assertRaises(ValueError):\n            sla_minutes_for("urgent-ish")\n''',
        "tests/test_ticket_service.py": '''import unittest\nfrom supportdesk.domain.customer import Customer\nfrom supportdesk.repositories.tickets import InMemoryTicketRepository\nfrom supportdesk.services.ticket_service import TicketService\n\nclass TicketServiceTests(unittest.TestCase):\n    def setUp(self):\n        self.repo = InMemoryTicketRepository()\n        self.service = TicketService(self.repo)\n\n    def test_create_ticket_persists_ticket(self):\n        ticket = self.service.create_ticket(Customer("C-1"), "high", "Need help")\n        self.assertEqual(ticket.id, "T-0001")\n        self.assertEqual(ticket.sla_minutes, 480)\n        self.assertIs(self.repo.get("T-0001"), ticket)\n\n    def test_summary_is_required(self):\n        with self.assertRaises(ValueError):\n            self.service.create_ticket(Customer("C-1"), "normal", "   ")\n''',
        "tests/test_ticket_api.py": '''import unittest\nfrom supportdesk.api.tickets import create_ticket_payload\nfrom supportdesk.domain.customer import Customer\nfrom supportdesk.repositories.tickets import InMemoryTicketRepository\nfrom supportdesk.services.ticket_service import TicketService\n\nclass TicketApiTests(unittest.TestCase):\n    def test_existing_payload_contract(self):\n        payload = create_ticket_payload(TicketService(InMemoryTicketRepository()), Customer("C-9"), "normal", "Question")\n        self.assertEqual(payload, {"id":"T-0001","customer_id":"C-9","severity":"normal","summary":"Question","sla_minutes":1440})\n''',
        "tests/test_customer.py": '''import unittest\nfrom supportdesk.domain.customer import Customer\n\nclass CustomerTests(unittest.TestCase):\n    def test_tier(self):\n        self.assertFalse(Customer("1").is_premium())\n        self.assertTrue(Customer("2", "premium").is_premium())\n''',
        "tests/test_repository.py": '''import unittest\nfrom supportdesk.repositories.tickets import InMemoryTicketRepository\n\nclass RepositoryTests(unittest.TestCase):\n    def test_missing_ticket(self):\n        self.assertIsNone(InMemoryTicketRepository().get("missing"))\n''',
        "tests/test_permissions.py": '''import unittest\nfrom supportdesk.auth.permissions import can_close_ticket, can_view_ticket\n\nclass PermissionTests(unittest.TestCase):\n    def test_permissions(self):\n        self.assertTrue(can_view_ticket("agent"))\n        self.assertFalse(can_close_ticket("agent"))\n''',
        "tests/test_reporting.py": '''import unittest\nfrom supportdesk.reporting.metrics import resolution_rate\n\nclass ReportingTests(unittest.TestCase):\n    def test_resolution_rate(self):\n        self.assertEqual(resolution_rate(2, 4), .5)\n''',
    }
    # Add realistic but irrelevant modules so retrieval has to discriminate context.
    for group in ("integrations", "exports", "imports", "analytics"):
        files[f"supportdesk/{group}/__init__.py"] = ""
        for index in range(1, 7):
            files[f"supportdesk/{group}/module_{index:02d}.py"] = (
                f'"""{group.title()} helper {index} for batch data normalization."""\n\n'
                f'def {group}_helper_{index}(value: str) -> str:\n'
                f'    """Normalize {group} value {index}."""\n'
                f'    return value.strip().lower()\n'
            )
    for rel, content in files.items():
        path=root/rel; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(content, encoding="utf-8")
    subprocess.run(["git","init","-b","main"],cwd=root,check=True,capture_output=True)
    subprocess.run(["git","config","user.email","dynosai@example.local"],cwd=root,check=True)
    subprocess.run(["git","config","user.name","DynosAI Brownfield Fixture"],cwd=root,check=True)
    subprocess.run(["git","add","-A"],cwd=root,check=True)
    subprocess.run(["git","commit","-m","feat: existing SupportDesk baseline"],cwd=root,check=True,capture_output=True)
    return {"project":"SupportDesk","files":len(files),"tests":7,"head":subprocess.run(["git","rev-parse","HEAD"],cwd=root,text=True,capture_output=True,check=True).stdout.strip()}


def apply_supportdesk_premium_sla(root: Path) -> list[str]:
    """Apply the expected brownfield feature implementation to an existing SupportDesk fixture.

    This helper is intentionally deterministic and is used only by local certification. Real
    `dynosai debug e2e --agent cursor --scenario supportdesk-premium-sla` still requires the
    coding agent to discover and implement the change itself.
    """
    updates = {
        "supportdesk/services/sla.py": '''SLA_MINUTES = {"critical": 240, "high": 480, "normal": 1440}\n\ndef sla_policy(customer, severity: str) -> tuple[int, bool]:\n    """Return SLA minutes and whether immediate escalation is required."""\n    try:\n        base = SLA_MINUTES[severity]\n    except KeyError as exc:\n        raise ValueError(f"unsupported severity: {severity}") from exc\n    if customer.is_premium() and severity == "critical":\n        return 60, True\n    return base, False\n\ndef sla_minutes_for(severity: str) -> int:\n    """Backward-compatible severity-only SLA lookup."""\n    try:\n        return SLA_MINUTES[severity]\n    except KeyError as exc:\n        raise ValueError(f"unsupported severity: {severity}") from exc\n''',
        "supportdesk/domain/ticket.py": '''from dataclasses import dataclass\n\n@dataclass(frozen=True)\nclass Ticket:\n    """Persisted support ticket with SLA and escalation decision chosen at creation time."""\n    id: str\n    customer_id: str\n    severity: str\n    summary: str\n    sla_minutes: int\n    escalation_required: bool = False\n''',
        "supportdesk/services/ticket_service.py": '''from supportdesk.domain.customer import Customer\nfrom supportdesk.domain.ticket import Ticket\nfrom supportdesk.services.sla import sla_policy\n\nclass TicketService:\n    """Create tickets using the customer-aware SLA policy."""\n    def __init__(self, repository):\n        self.repository = repository\n\n    def create_ticket(self, customer: Customer, severity: str, summary: str) -> Ticket:\n        if not summary.strip():\n            raise ValueError("summary is required")\n        minutes, escalation = sla_policy(customer, severity)\n        ticket = Ticket(\n            id=self.repository.next_id(), customer_id=customer.id, severity=severity,\n            summary=summary.strip(), sla_minutes=minutes, escalation_required=escalation,\n        )\n        self.repository.save(ticket)\n        return ticket\n''',
        "supportdesk/api/tickets.py": '''def create_ticket_payload(service, customer, severity: str, summary: str) -> dict:\n    """Application API adapter for ticket creation."""\n    ticket = service.create_ticket(customer, severity, summary)\n    return {\n        "id": ticket.id, "customer_id": ticket.customer_id, "severity": ticket.severity,\n        "summary": ticket.summary, "sla_minutes": ticket.sla_minutes,\n        "escalation_required": ticket.escalation_required,\n    }\n''',
        "tests/test_sla.py": '''import unittest\nfrom supportdesk.domain.customer import Customer\nfrom supportdesk.services.sla import sla_minutes_for, sla_policy\n\nclass SlaTests(unittest.TestCase):\n    def test_current_sla_values(self):\n        self.assertEqual(sla_minutes_for("critical"),240); self.assertEqual(sla_minutes_for("high"),480); self.assertEqual(sla_minutes_for("normal"),1440)\n    def test_premium_critical_policy(self):\n        self.assertEqual(sla_policy(Customer("1","premium"),"critical"),(60,True))\n        self.assertEqual(sla_policy(Customer("2"),"critical"),(240,False))\n        self.assertEqual(sla_policy(Customer("3","premium"),"high"),(480,False))\n        self.assertEqual(sla_policy(Customer("4","premium"),"normal"),(1440,False))\n    def test_unknown_severity_is_rejected(self):\n        with self.assertRaises(ValueError): sla_policy(Customer("1"),"urgent-ish")\n''',
        "tests/test_ticket_service.py": '''import unittest\nfrom supportdesk.domain.customer import Customer\nfrom supportdesk.repositories.tickets import InMemoryTicketRepository\nfrom supportdesk.services.ticket_service import TicketService\n\nclass TicketServiceTests(unittest.TestCase):\n    def setUp(self): self.repo=InMemoryTicketRepository(); self.service=TicketService(self.repo)\n    def test_create_ticket_persists_ticket(self):\n        t=self.service.create_ticket(Customer("C-1"),"high","Need help"); self.assertEqual((t.sla_minutes,t.escalation_required),(480,False)); self.assertIs(self.repo.get(t.id),t)\n    def test_premium_critical_is_escalated(self):\n        t=self.service.create_ticket(Customer("C-2","premium"),"critical","Outage"); self.assertEqual((t.sla_minutes,t.escalation_required),(60,True)); self.assertEqual(len(self.repo.items),1)\n    def test_summary_is_required(self):\n        with self.assertRaises(ValueError): self.service.create_ticket(Customer("C-1"),"normal","   ")\n''',
        "tests/test_ticket_api.py": '''import unittest\nfrom supportdesk.api.tickets import create_ticket_payload\nfrom supportdesk.domain.customer import Customer\nfrom supportdesk.repositories.tickets import InMemoryTicketRepository\nfrom supportdesk.services.ticket_service import TicketService\n\nclass TicketApiTests(unittest.TestCase):\n    def test_standard_payload_contract(self):\n        p=create_ticket_payload(TicketService(InMemoryTicketRepository()),Customer("C-9"),"normal","Question"); self.assertEqual(p,{"id":"T-0001","customer_id":"C-9","severity":"normal","summary":"Question","sla_minutes":1440,"escalation_required":False})\n    def test_premium_critical_payload(self):\n        p=create_ticket_payload(TicketService(InMemoryTicketRepository()),Customer("C-8","premium"),"critical","Outage"); self.assertEqual(p["sla_minutes"],60); self.assertTrue(p["escalation_required"])\n''',
    }
    for rel, content in updates.items():
        path = root / rel
        if not path.exists():
            raise FileNotFoundError(f"brownfield certification expected existing file: {rel}")
        path.write_text(content, encoding="utf-8")
    return sorted(updates)


def brownfield_orderflow_spec() -> dict[str, Any]:
    """Approved functional contract used by deterministic 0.7 certification."""
    return {
        "objective": (
            "Añadir descuentos contractuales vigentes para clientes enterprise en OrderFlow, evolucionando el esquema SQLite de forma aditiva, "
            "preservando pedidos existentes y manteniendo compatibilidad del contrato API mientras se exponen importes original, descuento y final."
        ),
        "scope": [
            "Migración forward-only del esquema SQLite existente",
            "Configuración de porcentaje y fecha de vigencia del descuento por cliente enterprise",
            "Cálculo y persistencia del descuento en pedidos nuevos",
            "Compatibilidad de lectura para pedidos creados antes de la migración",
            "Extensión compatible del payload API de creación",
            "Pruebas de migración, regresión, dominio, servicio y API",
        ],
        "out_of_scope": [
            "Descuentos por producto o cupón",
            "Cambiar pagos, facturación o notificaciones",
            "Recalcular o modificar retroactivamente pedidos históricos",
            "Servicios remotos o dependencias externas",
        ],
        "requirements": [
            {"id":"REQ-001","text":"El esquema SQLite existente debe evolucionar mediante una migración aditiva 002 que añada configuración contractual de descuento al cliente y desglose de importes al pedido sin eliminar ni renombrar columnas existentes.","priority":"must"},
            {"id":"REQ-002","text":"La migración debe conservar íntegramente clientes y pedidos existentes, ser idempotente a través del ledger de migraciones y no modificar total_cents de pedidos históricos.","priority":"must"},
            {"id":"REQ-003","text":"Un cliente de segmento enterprise con discount_percent mayor que cero y discount_valid_from menor o igual a la fecha del pedido debe recibir el descuento contractual en pedidos nuevos.","priority":"must"},
            {"id":"REQ-004","text":"Un descuento con fecha de vigencia futura no debe aplicarse, y los clientes standard no deben recibir descuento contractual aunque exista configuración de descuento.","priority":"must"},
            {"id":"REQ-005","text":"El descuento en céntimos debe calcularse como original_total_cents * discount_percent // 100; total_cents debe representar el importe final y nunca ser negativo.","priority":"must"},
            {"id":"REQ-006","text":"Los pedidos nuevos deben persistir original_total_cents, discount_cents y total_cents. Para pedidos históricos, las nuevas columnas original_total_cents y discount_cents deben permanecer físicamente NULL tras 002, sin backfill ni defaults que cambien su lectura raw; el repositorio debe interpretar esos NULL como total histórico y cero respectivamente.","priority":"must"},
            {"id":"REQ-007","text":"La API de creación debe conservar id, customer_id, total_cents y status y añadir original_total_cents y discount_cents sin eliminar campos existentes.","priority":"must"},
            {"id":"REQ-008","text":"La feature no debe modificar los módulos de payments, invoices, notifications ni reporting ni introducir nuevas dependencias externas.","priority":"should"},
        ],
        "acceptance_criteria": [
            {"id":"AC-001","requirement":"REQ-001","text":"Dada una base en versión 001, cuando se aplican las migraciones, entonces existe la versión 002 y las nuevas columnas de clientes y pedidos están disponibles sin perder las columnas anteriores."},
            {"id":"AC-002","requirement":"REQ-002","text":"Dado un cliente y un pedido insertados antes de 002, cuando se aplica 002 dos veces, entonces ambos registros siguen existiendo, total_cents conserva su valor original y el ledger contiene cada versión una sola vez."},
            {"id":"AC-003","requirement":"REQ-003","text":"Dado un cliente enterprise con 10 por ciento vigente y un pedido de 10000 céntimos, cuando se crea el pedido, entonces original_total_cents=10000, discount_cents=1000 y total_cents=9000."},
            {"id":"AC-004","requirement":"REQ-004","text":"Dado un descuento enterprise cuya vigencia es posterior a la fecha del pedido, cuando se crea el pedido, entonces discount_cents=0 y total_cents coincide con original_total_cents."},
            {"id":"AC-005","requirement":"REQ-004","text":"Dado un cliente standard con configuración de descuento, cuando se crea un pedido, entonces no se aplica descuento contractual."},
            {"id":"AC-006","requirement":"REQ-005","text":"Dado un total de 999 céntimos y un descuento vigente del 10 por ciento, cuando se calcula el descuento, entonces discount_cents=99 y total_cents=900 usando división entera de céntimos."},
            {"id":"AC-007","requirement":"REQ-006","text":"Dado un pedido histórico creado bajo 001, cuando se aplica 002, entonces original_total_cents y discount_cents permanecen físicamente NULL en la fila almacenada; cuando el repositorio lo carga, expone original_total_cents igual a total_cents y discount_cents igual a cero sin alterar esa fila."},
            {"id":"AC-008","requirement":"REQ-007","text":"Cuando la API crea un pedido con descuento, entonces mantiene los campos históricos id, customer_id, total_cents y status y añade original_total_cents y discount_cents con los valores persistidos."},
            {"id":"AC-009","requirement":"REQ-008","text":"Cuando se implementa la feature, entonces no se modifican archivos de payments, invoices, notifications ni reporting y las pruebas de regresión de esos módulos siguen pasando."},
        ],
        "business_rules": [
            {"id":"RULE-001","text":"Solo segment=enterprise puede activar un descuento contractual."},
            {"id":"RULE-002","text":"discount_valid_from es inclusiva y se compara como fecha ISO YYYY-MM-DD."},
            {"id":"RULE-003","text":"Los pedidos históricos no se recalculan; la migración es aditiva y preserva total_cents."},
            {"id":"RULE-004","text":"El redondeo del descuento se realiza hacia abajo al céntimo mediante aritmética entera."},
        ],
        "unknowns": [],
        "affected_features": [],
    }


def brownfield_orderflow_plan() -> dict[str, Any]:
    return {
        "approach": (
            "Evolucionar OrderFlow con una migración SQLite 002 estrictamente aditiva, ampliar los modelos/repositorios con valores por defecto compatibles, "
            "centralizar el cálculo contractual en pricing y propagar el desglose por OrderService y la API manteniendo intactos los dominios no relacionados."
        ),
        "architecture_delta": [
            "Añadir migrations/002_contract_discounts.sql sin reescribir 001 ni datos históricos.",
            "Ampliar Customer y Order con metadatos de descuento y defaults compatibles con construcciones existentes.",
            "Mantener el cálculo monetario puro en orderflow/services/pricing.py y la orquestación/persistencia en OrderService.",
            "Mantener total_cents como campo API histórico y semántica de importe final para pedidos nuevos.",
        ],
        "files": [
            {"path":"migrations/002_contract_discounts.sql","action":"create","role":"config","reason":"Migración aditiva de clientes y pedidos."},
            {"path":"orderflow/domain/customer.py","action":"modify","role":"source","reason":"Transportar porcentaje y vigencia con defaults compatibles."},
            {"path":"orderflow/domain/order.py","action":"modify","role":"source","reason":"Transportar desglose monetario preservando total_cents/status."},
            {"path":"orderflow/repositories/customers.py","action":"modify","role":"source","reason":"Persistir y leer configuración contractual tras migración."},
            {"path":"orderflow/repositories/orders.py","action":"modify","role":"source","reason":"Persistir nuevos importes y leer filas históricas de forma compatible."},
            {"path":"orderflow/services/pricing.py","action":"modify","role":"source","reason":"Calcular descuento vigente con aritmética entera."},
            {"path":"orderflow/services/order_service.py","action":"modify","role":"source","reason":"Aplicar pricing contractual al crear pedidos sin romper la firma existente."},
            {"path":"orderflow/api/orders.py","action":"modify","role":"source","reason":"Extender payload manteniendo campos históricos."},
            {"path":"tests/test_migrations.py","action":"modify","role":"test","reason":"Probar 001→002, preservación e idempotencia."},
            {"path":"tests/test_pricing.py","action":"modify","role":"test","reason":"Probar vigencia, segmento y redondeo."},
            {"path":"tests/test_order_service.py","action":"modify","role":"test","reason":"Probar persistencia de descuentos y compatibilidad de firma."},
            {"path":"tests/test_api.py","action":"modify","role":"test","reason":"Probar extensión compatible del contrato API."},
        ],
        "risks": [
            "Migrar columnas con defaults que alteren datos históricos.",
            "Aplicar descuentos antes de su vigencia o a clientes standard.",
            "Romper lectores existentes de Order o el payload histórico de la API.",
            "Introducir aritmética decimal inconsistente con el almacenamiento en céntimos.",
        ],
        "data_migration": {
            "required": True,
            "strategy": "Crear una migración 002 forward-only y aditiva con ALTER TABLE; el runner mantiene el ledger schema_migrations e ignora versiones ya aplicadas.",
            "data_preservation": "No actualizar filas históricas: total_cents y las claves existentes permanecen intactas y no se ejecuta ningún backfill durante 002.",
            "existing_row_semantics": "Para filas de orders creadas bajo 001, original_total_cents y discount_cents permanecen físicamente NULL después de 002; el fallback a total histórico y cero pertenece exclusivamente a la capa de lectura.",
            "backward_compatibility": "Conservar columnas, firma de create_order con nuevos argumentos solo opcionales y campos API históricos; los modelos nuevos añaden valores por defecto.",
            "verification_strategy": "Partir de una base 001 con filas históricas reales, capturar sus valores raw antes de 002, aplicar 002 dos veces y comprobar ledger, columnas, total_cents intacto, nuevas columnas raw NULL y fallback de repositorio.",
            "rollback_strategy": "La migración no se revierte destructivamente; ante rollback de aplicación se restaura el backup SQLite previo al despliegue o se mantiene el esquema aditivo sin usar las columnas nuevas.",
        },
        "validation_profiles": ["unit"],
        "tasks": [
            {
                "title":"Crear migración 002 y probar preservación",
                "description":"Añadir la migración SQLite aditiva y pruebas que parten de 001 con datos reales, aplican 002 dos veces y verifican esquema, ledger y preservación de filas históricas.",
                "requirements":["REQ-001","REQ-002"],
                "acceptance":["AC-001","AC-002"],
                "files":["migrations/002_contract_discounts.sql","tests/test_migrations.py"],
                "depends_on":[],
                "evidence_required":["git_diff","test_result","migration_result"],
            },
            {
                "title":"Evolucionar modelos y repositorios compatibles",
                "description":"Ampliar Customer/Order y sus repositorios para persistir configuración y desglose, con fallback de lectura de pedidos históricos y defaults compatibles.",
                "requirements":["REQ-006"],
                "acceptance":["AC-007"],
                "files":["orderflow/domain/customer.py","orderflow/domain/order.py","orderflow/repositories/customers.py","orderflow/repositories/orders.py","tests/test_order_service.py"],
                "depends_on":[0],
                "evidence_required":["git_diff","test_result"],
            },
            {
                "title":"Implementar pricing contractual vigente",
                "description":"Añadir cálculo puro de descuento para enterprise, fecha inclusiva y redondeo entero manteniendo el subtotal sin descuento para el resto de casos.",
                "requirements":["REQ-003","REQ-004","REQ-005"],
                "acceptance":["AC-003","AC-004","AC-005","AC-006"],
                "files":["orderflow/services/pricing.py","tests/test_pricing.py"],
                "depends_on":[1],
                "evidence_required":["git_diff","test_result"],
            },
            {
                "title":"Aplicar descuento al crear y persistir pedidos",
                "description":"Hacer que OrderService cargue el cliente, aplique pricing contractual y persista original, descuento y total final sin romper llamadas existentes ni modificar pedidos históricos.",
                "requirements":["REQ-003","REQ-004","REQ-005","REQ-006"],
                "acceptance":["AC-003","AC-004","AC-005","AC-006","AC-007"],
                "files":["orderflow/services/order_service.py","tests/test_order_service.py"],
                "depends_on":[2],
                "evidence_required":["git_diff","test_result"],
            },
            {
                "title":"Extender API y cerrar regresión multi-dominio",
                "description":"Añadir el desglose monetario al payload sin eliminar campos anteriores y demostrar que los módulos no relacionados permanecen intactos mediante la suite completa.",
                "requirements":["REQ-007","REQ-008"],
                "acceptance":["AC-008","AC-009"],
                "files":["orderflow/api/orders.py","tests/test_api.py"],
                "depends_on":[3],
                "evidence_required":["git_diff","test_result"],
            },
        ],
    }


def seed_orderflow_brownfield(root: Path) -> dict[str, Any]:
    """Create a SQLite-backed, multi-domain OrderFlow repository at schema v1."""
    root.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {
        ".gitignore": "__pycache__/\n*.py[cod]\n*.db\n.dynosai/\n",
        "README.md": "# OrderFlow\n\nExisting local order platform with SQLite migrations, repositories, services and API adapters.\n",
        "orderflow/__init__.py": '"""OrderFlow application."""\n',
        "orderflow/db.py": '''import sqlite3\nfrom pathlib import Path\n\ndef connect(path=None):\n    target=":memory:" if path in (None, ":memory:") else str(path)\n    conn=sqlite3.connect(target); conn.row_factory=sqlite3.Row; conn.execute("PRAGMA foreign_keys=ON"); return conn\n''',
        "orderflow/migrations.py": '''from pathlib import Path\n\ndef apply_migrations(conn, migrations_dir=None, upto=None):\n    root=Path(migrations_dir) if migrations_dir else Path(__file__).resolve().parents[1]/"migrations"\n    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, name TEXT NOT NULL)")\n    applied={row[0] for row in conn.execute("SELECT version FROM schema_migrations")}\n    for path in sorted(root.glob("[0-9][0-9][0-9]_*.sql")):\n        version=int(path.name.split("_",1)[0])\n        if upto is not None and version>int(upto): continue\n        if version in applied: continue\n        conn.executescript(path.read_text(encoding="utf-8"))\n        conn.execute("INSERT INTO schema_migrations(version,name) VALUES(?,?)",(version,path.name))\n        conn.commit(); applied.add(version)\n    return sorted(applied)\n''',
        "migrations/001_initial.sql": '''CREATE TABLE customers(\n id TEXT PRIMARY KEY,\n name TEXT NOT NULL,\n segment TEXT NOT NULL DEFAULT 'standard'\n);\nCREATE TABLE orders(\n id TEXT PRIMARY KEY,\n customer_id TEXT NOT NULL REFERENCES customers(id),\n total_cents INTEGER NOT NULL,\n status TEXT NOT NULL,\n created_at TEXT NOT NULL\n);\n''',
        "orderflow/domain/__init__.py": "",
        "orderflow/domain/customer.py": '''from dataclasses import dataclass\n\n@dataclass(frozen=True)\nclass Customer:\n    id: str\n    name: str\n    segment: str = "standard"\n''',
        "orderflow/domain/order.py": '''from dataclasses import dataclass\n\n@dataclass(frozen=True)\nclass Order:\n    id: str\n    customer_id: str\n    total_cents: int\n    status: str = "created"\n''',
        "orderflow/repositories/__init__.py": "",
        "orderflow/repositories/customers.py": '''from orderflow.domain.customer import Customer\n\nclass CustomerRepository:\n    def __init__(self, conn): self.conn=conn\n    def save(self, customer: Customer):\n        self.conn.execute("INSERT INTO customers(id,name,segment) VALUES(?,?,?)",(customer.id,customer.name,customer.segment)); self.conn.commit()\n    def get(self, customer_id: str):\n        row=self.conn.execute("SELECT id,name,segment FROM customers WHERE id=?",(customer_id,)).fetchone()\n        return Customer(row["id"],row["name"],row["segment"]) if row else None\n''',
        "orderflow/repositories/orders.py": '''from orderflow.domain.order import Order\n\nclass OrderRepository:\n    def __init__(self, conn): self.conn=conn\n    def next_id(self):\n        return f"O-{self.conn.execute('SELECT COUNT(*) FROM orders').fetchone()[0]+1:04d}"\n    def save(self, order: Order, created_at: str):\n        self.conn.execute("INSERT INTO orders(id,customer_id,total_cents,status,created_at) VALUES(?,?,?,?,?)",(order.id,order.customer_id,order.total_cents,order.status,created_at)); self.conn.commit()\n    def get(self, order_id: str):\n        row=self.conn.execute("SELECT id,customer_id,total_cents,status FROM orders WHERE id=?",(order_id,)).fetchone()\n        return Order(row["id"],row["customer_id"],row["total_cents"],row["status"]) if row else None\n''',
        "orderflow/services/__init__.py": "",
        "orderflow/services/pricing.py": '''def subtotal_cents(line_items: list[int]) -> int:\n    if not line_items or any((not isinstance(x,int) or x<0) for x in line_items): raise ValueError("line_items must be non-negative integer cents")\n    return sum(line_items)\n''',
        "orderflow/services/order_service.py": '''from datetime import date\nfrom orderflow.domain.order import Order\nfrom orderflow.services.pricing import subtotal_cents\n\nclass OrderService:\n    def __init__(self, customer_repository, order_repository): self.customers=customer_repository; self.orders=order_repository\n    def create_order(self, customer_id: str, line_items: list[int], ordered_at: str|None=None):\n        if not self.customers.get(customer_id): raise ValueError("customer not found")\n        total=subtotal_cents(line_items); created=ordered_at or date.today().isoformat()\n        order=Order(self.orders.next_id(),customer_id,total,"created"); self.orders.save(order,created); return order\n''',
        "orderflow/api/__init__.py": "",
        "orderflow/api/orders.py": '''def create_order_payload(service, customer_id: str, line_items: list[int], ordered_at: str|None=None):\n    order=service.create_order(customer_id,line_items,ordered_at=ordered_at)\n    return {"id":order.id,"customer_id":order.customer_id,"total_cents":order.total_cents,"status":order.status}\n''',
        "orderflow/payments/__init__.py": "",
        "orderflow/payments/service.py": '''def payment_reference(order_id: str) -> str:\n    return f"PAY-{order_id}"\n''',
        "orderflow/invoices/__init__.py": "",
        "orderflow/invoices/service.py": '''def invoice_reference(order_id: str) -> str:\n    return f"INV-{order_id}"\n''',
        "orderflow/notifications/__init__.py": "",
        "orderflow/notifications/service.py": '''def notification_topic(order_id: str) -> str:\n    return f"orders/{order_id}"\n''',
        "orderflow/reporting/__init__.py": "",
        "orderflow/reporting/metrics.py": '''def average_total(values): return 0 if not values else sum(values)/len(values)\n''',
        "tests/__init__.py": "",
        "tests/test_migrations.py": '''import tempfile, unittest\nfrom pathlib import Path\nfrom orderflow.db import connect\nfrom orderflow.migrations import apply_migrations\n\nclass MigrationTests(unittest.TestCase):\n def test_initial_migration_is_idempotent(self):\n  with tempfile.TemporaryDirectory() as d:\n   conn=connect(); self.assertEqual(apply_migrations(conn),[1]); self.assertEqual(apply_migrations(conn),[1])\n   tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}; self.assertTrue({"customers","orders","schema_migrations"}<=tables)\n''',
        "tests/test_pricing.py": '''import unittest\nfrom orderflow.services.pricing import subtotal_cents\nclass PricingTests(unittest.TestCase):\n def test_subtotal(self): self.assertEqual(subtotal_cents([500,250]),750)\n def test_bad_items(self):\n  with self.assertRaises(ValueError): subtotal_cents([])\n''',
        "tests/test_order_service.py": '''import tempfile, unittest\nfrom pathlib import Path\nfrom orderflow.db import connect\nfrom orderflow.domain.customer import Customer\nfrom orderflow.migrations import apply_migrations\nfrom orderflow.repositories.customers import CustomerRepository\nfrom orderflow.repositories.orders import OrderRepository\nfrom orderflow.services.order_service import OrderService\n\nclass OrderServiceTests(unittest.TestCase):\n def test_existing_create_order_contract(self):\n  with tempfile.TemporaryDirectory() as d:\n   conn=connect(); apply_migrations(conn); customers=CustomerRepository(conn); orders=OrderRepository(conn); customers.save(Customer("C-1","Acme","enterprise"))\n   order=OrderService(customers,orders).create_order("C-1",[5000,5000],ordered_at="2026-08-13"); self.assertEqual(order.total_cents,10000); self.assertEqual(orders.get(order.id),order)\n''',
        "tests/test_api.py": '''import tempfile, unittest\nfrom pathlib import Path\nfrom orderflow.api.orders import create_order_payload\nfrom orderflow.db import connect\nfrom orderflow.domain.customer import Customer\nfrom orderflow.migrations import apply_migrations\nfrom orderflow.repositories.customers import CustomerRepository\nfrom orderflow.repositories.orders import OrderRepository\nfrom orderflow.services.order_service import OrderService\n\nclass ApiTests(unittest.TestCase):\n def test_existing_payload(self):\n  with tempfile.TemporaryDirectory() as d:\n   conn=connect(); apply_migrations(conn); cr=CustomerRepository(conn); cr.save(Customer("C-1","Acme")); payload=create_order_payload(OrderService(cr,OrderRepository(conn)),"C-1",[1000],"2026-08-13")\n   self.assertEqual(payload,{"id":"O-0001","customer_id":"C-1","total_cents":1000,"status":"created"})\n''',
        "tests/test_repositories.py": '''import tempfile, unittest\nfrom pathlib import Path\nfrom orderflow.db import connect\nfrom orderflow.domain.customer import Customer\nfrom orderflow.migrations import apply_migrations\nfrom orderflow.repositories.customers import CustomerRepository\nclass RepositoryTests(unittest.TestCase):\n def test_customer_roundtrip(self):\n  with tempfile.TemporaryDirectory() as d:\n   c=connect(); apply_migrations(c); r=CustomerRepository(c); r.save(Customer("1","One")); self.assertEqual(r.get("1").name,"One")\n''',
        "tests/test_other_domains.py": '''import unittest\nfrom orderflow.invoices.service import invoice_reference\nfrom orderflow.notifications.service import notification_topic\nfrom orderflow.payments.service import payment_reference\nfrom orderflow.reporting.metrics import average_total\nclass OtherDomainTests(unittest.TestCase):\n def test_unchanged_helpers(self):\n  self.assertEqual(payment_reference("O-1"),"PAY-O-1"); self.assertEqual(invoice_reference("O-1"),"INV-O-1"); self.assertEqual(notification_topic("O-1"),"orders/O-1"); self.assertEqual(average_total([10,20]),15)\n''',
    }
    # Larger irrelevant surface: the test should retrieve DB/order context, not scan everything.
    for group in ("catalog", "imports", "exports", "analytics", "fulfillment", "crm", "taxes"):
        files[f"orderflow/{group}/__init__.py"] = ""
        for index in range(1, 8):
            files[f"orderflow/{group}/module_{index:02d}.py"] = (
                f'"""{group.title()} helper {index}; unrelated to order discounts."""\n\n'
                f'def {group}_helper_{index}(value: str) -> str:\n'
                f'    return value.strip().lower()\n'
            )
    for rel, content in files.items():
        path=root/rel; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(content, encoding="utf-8")
    subprocess.run(["git","init","-b","main"],cwd=root,check=True,capture_output=True)
    subprocess.run(["git","config","user.email","dynosai@example.local"],cwd=root,check=True)
    subprocess.run(["git","config","user.name","DynosAI OrderFlow Fixture"],cwd=root,check=True)
    subprocess.run(["git","add","-A"],cwd=root,check=True)
    subprocess.run(["git","commit","-m","feat: existing OrderFlow schema v1 baseline"],cwd=root,check=True,capture_output=True)
    # Prove the repository is healthy before DynosAI adoption.
    baseline=subprocess.run(["python","-m","unittest","discover","-s","tests"],cwd=root,text=True,capture_output=True)
    if baseline.returncode != 0:
        raise RuntimeError("OrderFlow fixture baseline tests failed: "+baseline.stdout+baseline.stderr)
    return {
        "project":"OrderFlow","files":len(files),"tests":6,
        "head":subprocess.run(["git","rev-parse","HEAD"],cwd=root,text=True,capture_output=True,check=True).stdout.strip(),
        "schema_version":1,
    }


def apply_orderflow_contract_discounts(root: Path) -> list[str]:
    """Deterministic expected 0.7 implementation; real Cursor must discover it itself."""
    updates = {
        "migrations/002_contract_discounts.sql": '''ALTER TABLE customers ADD COLUMN discount_percent INTEGER;\nALTER TABLE customers ADD COLUMN discount_valid_from TEXT;\nALTER TABLE orders ADD COLUMN original_total_cents INTEGER;\nALTER TABLE orders ADD COLUMN discount_cents INTEGER;\n''',
        "orderflow/domain/customer.py": '''from dataclasses import dataclass\n\n@dataclass(frozen=True)\nclass Customer:\n    id: str\n    name: str\n    segment: str = "standard"\n    discount_percent: int = 0\n    discount_valid_from: str|None = None\n''',
        "orderflow/domain/order.py": '''from dataclasses import dataclass\n\n@dataclass(frozen=True)\nclass Order:\n    id: str\n    customer_id: str\n    total_cents: int\n    status: str = "created"\n    original_total_cents: int|None = None\n    discount_cents: int = 0\n''',
        "orderflow/repositories/customers.py": '''from orderflow.domain.customer import Customer\n\nclass CustomerRepository:\n    def __init__(self, conn): self.conn=conn\n    def save(self, customer: Customer):\n        self.conn.execute("INSERT INTO customers(id,name,segment,discount_percent,discount_valid_from) VALUES(?,?,?,?,?)",(customer.id,customer.name,customer.segment,customer.discount_percent,customer.discount_valid_from)); self.conn.commit()\n    def get(self, customer_id: str):\n        row=self.conn.execute("SELECT id,name,segment,discount_percent,discount_valid_from FROM customers WHERE id=?",(customer_id,)).fetchone()\n        return Customer(row["id"],row["name"],row["segment"],row["discount_percent"],row["discount_valid_from"]) if row else None\n''',
        "orderflow/repositories/orders.py": '''from orderflow.domain.order import Order\n\nclass OrderRepository:\n    def __init__(self, conn): self.conn=conn\n    def next_id(self): return f"O-{self.conn.execute('SELECT COUNT(*) FROM orders').fetchone()[0]+1:04d}"\n    def save(self, order: Order, created_at: str):\n        original=order.original_total_cents if order.original_total_cents is not None else order.total_cents\n        self.conn.execute("INSERT INTO orders(id,customer_id,total_cents,status,created_at,original_total_cents,discount_cents) VALUES(?,?,?,?,?,?,?)",(order.id,order.customer_id,order.total_cents,order.status,created_at,original,order.discount_cents)); self.conn.commit()\n    def get(self, order_id: str):\n        row=self.conn.execute("SELECT id,customer_id,total_cents,status,original_total_cents,discount_cents FROM orders WHERE id=?",(order_id,)).fetchone()\n        if not row: return None\n        original=row["original_total_cents"] if row["original_total_cents"] is not None else row["total_cents"]\n        return Order(row["id"],row["customer_id"],row["total_cents"],row["status"],original,row["discount_cents"] or 0)\n''',
        "orderflow/services/pricing.py": '''def subtotal_cents(line_items: list[int]) -> int:\n    if not line_items or any((not isinstance(x,int) or x<0) for x in line_items): raise ValueError("line_items must be non-negative integer cents")\n    return sum(line_items)\n\ndef price_for_customer(customer, line_items: list[int], ordered_at: str) -> tuple[int,int,int]:\n    original=subtotal_cents(line_items); percent=0\n    if customer.segment=="enterprise" and customer.discount_percent>0 and customer.discount_valid_from and customer.discount_valid_from<=ordered_at:\n        percent=min(customer.discount_percent,100)\n    discount=original*percent//100\n    return original,discount,max(0,original-discount)\n''',
        "orderflow/services/order_service.py": '''from datetime import date\nfrom orderflow.domain.order import Order\nfrom orderflow.services.pricing import price_for_customer\n\nclass OrderService:\n    def __init__(self, customer_repository, order_repository): self.customers=customer_repository; self.orders=order_repository\n    def create_order(self, customer_id: str, line_items: list[int], ordered_at: str|None=None):\n        customer=self.customers.get(customer_id)\n        if not customer: raise ValueError("customer not found")\n        created=ordered_at or date.today().isoformat(); original,discount,total=price_for_customer(customer,line_items,created)\n        order=Order(self.orders.next_id(),customer_id,total,"created",original,discount); self.orders.save(order,created); return order\n''',
        "orderflow/api/orders.py": '''def create_order_payload(service, customer_id: str, line_items: list[int], ordered_at: str|None=None):\n    order=service.create_order(customer_id,line_items,ordered_at=ordered_at)\n    return {"id":order.id,"customer_id":order.customer_id,"total_cents":order.total_cents,"status":order.status,"original_total_cents":order.original_total_cents,"discount_cents":order.discount_cents}\n''',
        "tests/test_migrations.py": '''import tempfile, unittest\nfrom pathlib import Path\nfrom orderflow.db import connect\nfrom orderflow.migrations import apply_migrations\n\nclass MigrationTests(unittest.TestCase):\n def test_001_to_002_preserves_existing_data_and_is_idempotent(self):\n  with tempfile.TemporaryDirectory() as d:\n   conn=connect(); self.assertEqual(apply_migrations(conn,upto=1),[1]); conn.execute("INSERT INTO customers(id,name,segment) VALUES('C-OLD','Legacy','enterprise')"); conn.execute("INSERT INTO orders(id,customer_id,total_cents,status,created_at) VALUES('O-OLD','C-OLD',7777,'created','2025-01-01')"); conn.commit()\n   self.assertEqual(apply_migrations(conn),[1,2]); self.assertEqual(apply_migrations(conn),[1,2]); row=conn.execute("SELECT total_cents,original_total_cents,discount_cents FROM orders WHERE id='O-OLD'").fetchone(); self.assertEqual((row[0],row[1],row[2]),(7777,None,None)); self.assertEqual(conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0],2)\n''',
        "tests/test_pricing.py": '''import unittest\nfrom orderflow.domain.customer import Customer\nfrom orderflow.services.pricing import price_for_customer, subtotal_cents\nclass PricingTests(unittest.TestCase):\n def test_subtotal(self): self.assertEqual(subtotal_cents([500,250]),750)\n def test_enterprise_active_discount(self): self.assertEqual(price_for_customer(Customer("1","Acme","enterprise",10,"2026-01-01"),[5000,5000],"2026-08-13"),(10000,1000,9000))\n def test_future_and_standard_do_not_discount(self):\n  self.assertEqual(price_for_customer(Customer("1","Acme","enterprise",10,"2027-01-01"),[1000],"2026-08-13"),(1000,0,1000)); self.assertEqual(price_for_customer(Customer("2","Std","standard",10,"2020-01-01"),[1000],"2026-08-13"),(1000,0,1000))\n def test_integer_rounding(self): self.assertEqual(price_for_customer(Customer("1","Acme","enterprise",10,"2020-01-01"),[999],"2026-08-13"),(999,99,900))\n''',
        "tests/test_order_service.py": '''import tempfile, unittest\nfrom pathlib import Path\nfrom orderflow.db import connect\nfrom orderflow.domain.customer import Customer\nfrom orderflow.migrations import apply_migrations\nfrom orderflow.repositories.customers import CustomerRepository\nfrom orderflow.repositories.orders import OrderRepository\nfrom orderflow.services.order_service import OrderService\n\nclass OrderServiceTests(unittest.TestCase):\n def test_discounted_order_persists_breakdown(self):\n  with tempfile.TemporaryDirectory() as d:\n   conn=connect(); apply_migrations(conn); cr=CustomerRepository(conn); orp=OrderRepository(conn); cr.save(Customer("C-1","Acme","enterprise",10,"2026-01-01")); order=OrderService(cr,orp).create_order("C-1",[5000,5000],"2026-08-13"); self.assertEqual((order.original_total_cents,order.discount_cents,order.total_cents),(10000,1000,9000)); self.assertEqual(orp.get(order.id),order)\n def test_historical_order_fallback(self):\n  with tempfile.TemporaryDirectory() as d:\n   conn=connect(); apply_migrations(conn,upto=1); conn.execute("INSERT INTO customers(id,name,segment) VALUES('C-OLD','Legacy','standard')"); conn.execute("INSERT INTO orders(id,customer_id,total_cents,status,created_at) VALUES('O-OLD','C-OLD',7777,'created','2025-01-01')"); conn.commit(); apply_migrations(conn); old=OrderRepository(conn).get("O-OLD"); self.assertEqual((old.original_total_cents,old.discount_cents,old.total_cents),(7777,0,7777))\n def test_existing_signature_no_discount(self):\n  with tempfile.TemporaryDirectory() as d:\n   conn=connect(); apply_migrations(conn); cr=CustomerRepository(conn); cr.save(Customer("C-2","Standard")); order=OrderService(cr,OrderRepository(conn)).create_order("C-2",[1000]); self.assertEqual((order.original_total_cents,order.discount_cents,order.total_cents),(1000,0,1000))\n''',
        "tests/test_api.py": '''import tempfile, unittest\nfrom pathlib import Path\nfrom orderflow.api.orders import create_order_payload\nfrom orderflow.db import connect\nfrom orderflow.domain.customer import Customer\nfrom orderflow.migrations import apply_migrations\nfrom orderflow.repositories.customers import CustomerRepository\nfrom orderflow.repositories.orders import OrderRepository\nfrom orderflow.services.order_service import OrderService\n\nclass ApiTests(unittest.TestCase):\n def test_payload_extends_legacy_contract(self):\n  with tempfile.TemporaryDirectory() as d:\n   conn=connect(); apply_migrations(conn); cr=CustomerRepository(conn); cr.save(Customer("C-1","Acme","enterprise",10,"2026-01-01")); p=create_order_payload(OrderService(cr,OrderRepository(conn)),"C-1",[10000],"2026-08-13"); self.assertEqual({k:p[k] for k in ("id","customer_id","total_cents","status")},{"id":"O-0001","customer_id":"C-1","total_cents":9000,"status":"created"}); self.assertEqual((p["original_total_cents"],p["discount_cents"]),(10000,1000))\n''',
    }
    for rel, content in updates.items():
        path=root/rel
        if rel != "migrations/002_contract_discounts.sql" and not path.exists():
            raise FileNotFoundError(f"OrderFlow certification expected existing file: {rel}")
        path.parent.mkdir(parents=True, exist_ok=True); path.write_text(content, encoding="utf-8")
    return sorted(updates)


def greenfield_fibonacci_spec() -> dict[str, Any]:
    return {
        "objective":"Crear una aplicación CLI Python que imprima exactamente N términos de Fibonacci con contrato de errores determinista.",
        "scope":["Paquete Python fibonacci","Entrada CLI mediante python -m fibonacci N","Pruebas unitarias y de comportamiento CLI"],
        "out_of_scope":["Persistencia","UI gráfica","APIs remotas"],
        "requirements":[
            {"id":"REQ-001","text":"La aplicación debe ejecutarse como python -m fibonacci N y aceptar exactamente un argumento N.","priority":"must"},
            {"id":"REQ-002","text":"Para N mayor que cero debe imprimir exactamente N términos comenzando por 0 1 1 2, separados por un espacio y un salto de línea final.","priority":"must"},
            {"id":"REQ-003","text":"N=0 debe ser válido y producir stdout completamente vacío con código de salida cero.","priority":"must"},
            {"id":"REQ-004","text":"Valores negativos, no numéricos, fraccionarios, argumentos ausentes o adicionales deben producir stderr no vacío y exit code 1.","priority":"must"},
            {"id":"REQ-005","text":"Un signo + opcional en un entero base 10 debe aceptarse, por ejemplo +4 equivale a 4.","priority":"must"},
        ],
        "acceptance_criteria":[
            {"id":"AC-001","requirement":"REQ-001","text":"Cuando se ejecuta python -m fibonacci 5, entonces el proceso acepta el único argumento y termina con código cero."},
            {"id":"AC-002","requirement":"REQ-002","text":"Cuando N=5, entonces stdout es exactamente 0 1 1 2 3 seguido de un único salto de línea."},
            {"id":"AC-003","requirement":"REQ-003","text":"Cuando N=0, entonces stdout está vacío y el código de salida es cero."},
            {"id":"AC-004","requirement":"REQ-004","text":"Cuando N es -1, abc o 3.5, entonces stderr no está vacío y el código de salida es uno."},
            {"id":"AC-005","requirement":"REQ-004","text":"Cuando falta N o hay argumentos adicionales, entonces stderr no está vacío y el código de salida es uno."},
            {"id":"AC-006","requirement":"REQ-005","text":"Cuando N=+4, entonces stdout contiene exactamente 0 1 1 2 y el código de salida es cero."},
        ],
        "business_rules":[{"id":"RULE-001","text":"F0=0 y F1=1; N representa cantidad de términos, no índice máximo."}],
        "unknowns":[],"affected_features":[],
    }


def greenfield_fibonacci_plan() -> dict[str, Any]:
    return {
        "approach":"Separar el cálculo puro de Fibonacci de la adaptación CLI y validar ambos contratos mediante unittest y casos black-box del harness.",
        "architecture_delta":["Crear paquete fibonacci con core puro y __main__ como adaptador CLI."],
        "files":[
            {"path":"fibonacci/__init__.py","action":"create","role":"source","reason":"Paquete y export público."},
            {"path":"fibonacci/core.py","action":"create","role":"source","reason":"Generación pura de secuencia."},
            {"path":"fibonacci/__main__.py","action":"create","role":"source","reason":"Parsing y salida CLI."},
            {"path":"tests/test_fibonacci.py","action":"create","role":"test","reason":"Regresión del núcleo y CLI."},
        ],
        "risks":["Confundir N con índice máximo","Emitir newline para N=0","Aceptar argumentos extra accidentalmente"],
        "validation_profiles":["unit"],
        "tasks":[
            {"title":"Implementar núcleo Fibonacci","description":"Crear la generación pura de exactamente N términos con validación de N entero no negativo.","requirements":["REQ-002","REQ-003"],"acceptance":["AC-002","AC-003"],"files":["fibonacci/__init__.py","fibonacci/core.py","tests/test_fibonacci.py"],"depends_on":[],"evidence_required":["git_diff","test_result"]},
            {"title":"Implementar contrato CLI","description":"Crear __main__ con parsing estricto, signo positivo opcional, stderr y códigos de salida definidos.","requirements":["REQ-001","REQ-004","REQ-005"],"acceptance":["AC-001","AC-004","AC-005","AC-006"],"files":["fibonacci/__main__.py","tests/test_fibonacci.py"],"depends_on":[0],"evidence_required":["git_diff","test_result"]},
        ],
    }


def apply_greenfield_fibonacci(root: Path) -> list[str]:
    updates={
        "fibonacci/__init__.py":'''from .core import generate\n__all__=["generate"]\n''',
        "fibonacci/core.py":'''def generate(n: int) -> list[int]:\n    if not isinstance(n,int) or isinstance(n,bool) or n<0: raise ValueError("N must be a non-negative integer")\n    out=[]; a,b=0,1\n    for _ in range(n): out.append(a); a,b=b,a+b\n    return out\n''',
        "fibonacci/__main__.py":'''import sys\nfrom fibonacci.core import generate\n\ndef main(argv=None):\n    argv=list(sys.argv[1:] if argv is None else argv)\n    if len(argv)!=1:\n        print("usage: python -m fibonacci N",file=sys.stderr); return 1\n    raw=argv[0]\n    if not raw or raw.startswith("-") or not raw.lstrip("+").isdigit():\n        print("N must be a non-negative base-10 integer",file=sys.stderr); return 1\n    try: values=generate(int(raw,10))\n    except ValueError as exc: print(str(exc),file=sys.stderr); return 1\n    if values: sys.stdout.write(" ".join(map(str,values))+"\\n")\n    return 0\n\nif __name__=="__main__": raise SystemExit(main())\n''',
        "tests/test_fibonacci.py":'''import subprocess, sys, unittest\nfrom fibonacci.core import generate\nclass FibonacciTests(unittest.TestCase):\n def test_generate(self): self.assertEqual(generate(5),[0,1,1,2,3]); self.assertEqual(generate(0),[])\n def test_invalid_core(self):\n  with self.assertRaises(ValueError): generate(-1)\n def test_cli(self):\n  p=subprocess.run([sys.executable,"-m","fibonacci","+4"],text=True,capture_output=True); self.assertEqual((p.returncode,p.stdout),(0,"0 1 1 2\\n"))\n''',
    }
    for rel,content in updates.items():
        path=root/rel; path.parent.mkdir(parents=True,exist_ok=True); path.write_text(content,encoding="utf-8")
    return sorted(updates)
