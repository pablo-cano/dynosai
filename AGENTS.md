<!-- DYNOSAI:START -->
# DynosAI Provider-Native Protocol

Este proyecto está gobernado por DynosAI. El agente escribe código; DynosAI Core gobierna workflow, memoria, scope, Git, validación y evidencia.

1. Para una necesidad funcional usa el MCP de DynosAI; no ejecutes `dynosai open` ni cambies de aplicación.
2. Una feature usa una única sesión persistente del proveedor desde Discovery hasta Done.
3. Consulta `dynosai_get_next_action`; su `contract` es autoritativo.
4. Las decisiones humanas (clarificaciones, Spec Review, Plan Review, scope, Code Review y Merge) se resuelven mediante MCP Elicitation. Nunca autoapruebes un gate.
5. No escribas código antes de que el estado sea `implementing`. Durante implementación respeta estrictamente el scope devuelto por DynosAI.
6. Usa `dynosai_submit_spec`, `dynosai_submit_plan` y `dynosai_register_result`; DynosAI valida cada contrato y evidencia.
7. Para contexto: reutiliza primero `context_checkpoint` y el `task_queue` actual; carga sólo secciones faltantes con `dynosai_checkpoint`. Usa `dynosai_ask` para memoria/SQL/texto, `dynosai_find_symbol` para identificadores y `dynosai_read` sólo para contenido concreto que aún no esté consolidado.
8. Git de escritura pertenece al controlador DynosAI; el agente no hace branch/commit/merge por su cuenta.
9. Las validaciones se ejecutan mediante `dynosai_run_validation` y perfiles aprobados.
10. Si la sesión del proveedor se reinicia, usa `dynosai_resume`; la fuente de verdad es `.dynosai/knowledge.db`, no el historial del chat.
11. Los Markdown son vistas regenerables.
<!-- DYNOSAI:END -->
