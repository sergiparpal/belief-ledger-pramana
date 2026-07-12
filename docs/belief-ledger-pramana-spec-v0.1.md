# Belief Ledger tipado (pramāṇa) — Especificación v0.1-draft

**Qué es:** middleware de harness que mantiene, para cada episodio de agente, un libro mayor de creencias tipadas por fuente epistémica, con grafo de justificaciones, motor de derrota (bādha) y política de confianza explícita (svataḥ/parataḥ). Se integra en el bucle del agente en cinco puntos y produce el bloque de contexto epistémico que ve el modelo.

**Linaje técnico:** JTMS (Doyle 1979) para el mantenimiento de razones, defeaters de Pollock (rebutting/undercutting) para la semántica de derrota, AGM como referencia de revisión, con los pramāṇas como esquema de procedencia y las condiciones de validez clásicas (āpta, vyāpti, yogyatā) operacionalizadas como checks.

---

## 0. Alcance y no-objetivos

**Objetivos.** (1) Que toda afirmación fáctica que el agente use o emita sea trazable a una creencia con tipo, procedencia y estado. (2) Que la llegada de evidencia contradictoria produzca retractación estructural y propagada, no una corrección retórica. (3) Que la decisión confiar-por-defecto vs. verificar-antes sea configuración explícita por fuente × stakes, no un accidente del prompt.

**No-objetivos (v0.x).**
- **No es una defensa anti-inyección.** La integridad de canal se marca en la ingesta y condiciona prioridades, pero la defensa contra instrucciones embebidas en contenido es una capa ortogonal del harness. El ledger la asume, no la implementa.
- **No es un razonador probabilístico.** El estado de una creencia es discreto (IN/OUT/PENDING/QUARANTINED); la confianza escalar existe como campo auxiliar pero no gobierna la derrota. Racional en §4.
- **No es un knowledge graph.** No se exige ontología ni forma lógica: las creencias son proposiciones en lenguaje natural normalizado. La estructura vive en el grafo de justificaciones y derrotas, no en el contenido.
- **No es memoria a largo plazo.** El ledger es por-episodio (o por-tarea). Su interacción con memoria persistente se define en la regla R6 y el resto queda para el proyecto vāsanā-store.

---

## 1. Principios de diseño (reglas no negociables)

**R1 — Estado discreto, derrota estructural.** La pregunta operativa no es "¿cuán probable es P?" sino "¿P está actualmente sostenida, por qué cadena, y qué la derrotaría?". Los estados discretos hacen la retractación computable y auditable (transiciones con causa registrada). La probabilidad puede añadirse encima; al revés no funciona: un logprob no te dice qué retirar cuando llega el defeater.

**R2 — Wrapper/contenido.** Leer no es saber. La observación directa de una herramienta cubre exactamente lo que la herramienta mide. Un fetch de página produce *dos* creencias de tipos distintos: pratyakṣa («la URL U devolvió 200 con cuerpo que contiene T», fuente = el propio tool) y śabda («lo que T afirma», fuente = el dominio de U, con su āpta). Confundir ambas es el error de tipado nº1 de los sistemas RAG y la puerta de entrada de la mitad de las alucinaciones "citadas".

**R3 — La ausencia tiene condición de validez (yogyatā).** «No encontrado» solo es evidencia de ausencia si el buscador habría encontrado el objeto de estar presente. Toda creencia anupalabdhi debe llevar adjunta una estimación de detectabilidad (cobertura del corpus para esa clase de query × recall estimado del retriever, con parámetros de búsqueda registrados). Si la condición no se cumple, el resultado se registra como evento `SEARCH_FAILED` (evidencia sobre la búsqueda), nunca como creencia sobre el mundo.

**R4 — La memoria no es un pramāṇa.** Una creencia recuperada de un episodio anterior no es conocimiento nuevo: reentra con su tipo original, sus qualifiers temporales, y un descuento por perecibilidad; debe volver a pasar las condiciones de validez de su tipo. La memoria es transporte, no fuente.

**R5 — El monitor es contenido.** El extractor de claims, el linter y los verificadores son a su vez procesos falibles cuyos veredictos se registran como creencias de tipo anumāna con fuente = ese componente, auditables y con estadísticas propias. Ningún componente del sistema tiene estatus de testigo privilegiado.

**R6 — Independencia de testimonios.** Para corroboración y para verificación parataḥ(k), dos testimonios cuentan como independientes solo si sus raíces de procedencia difieren (dominio/origen distinto) y su contenido no es casi-duplicado (dedup por similitud). N chunks del mismo mirror son un testigo, no N.

**R7 — Qualifiers antes que contradicción.** Antes de declarar REBUT, se normalizan los ámbitos: «X a fecha 2024» y «X a fecha 2026» no se contradicen; «según la doc oficial» y «según el comportamiento observado» pueden coexistir marcadas. Muchas contradicciones aparentes son desajustes de scope; el detector debe reconciliar qualifiers primero.

**R8 — Event-sourcing.** Todo es append-only: las creencias son vistas materializadas sobre un log de eventos. La auditabilidad no es una feature, es el formato de almacenamiento.

---

## 2. Modelo de datos

### 2.1 Entidades

```python
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime

class SourceKind(str, Enum):
    TOOL = "tool"            # ejecutores: shell, python, http, fs...
    RETRIEVER = "retriever"  # índices RAG propios
    WEB = "web"
    DOCUMENT = "document"    # docs aportados al episodio
    USER = "user"
    MODEL = "model"          # el propio LLM u otros agentes/componentes
    LEDGER = "ledger"        # re-ingesta desde episodios previos (R4)

class Integrity(str, Enum):
    TRUSTED = "trusted"      # canal controlado por el operador
    SEMI = "semi"            # tercero con reputación establecida
    UNTRUSTED = "untrusted"  # web abierta / contenido inyectable

@dataclass
class Source:
    id: str
    kind: SourceKind
    integrity: Integrity
    competence: dict[str, float]      # dominio -> [0,1]; prior editable (āpta)
    stats: "SourceStats"              # confirmaciones, derrotas recibidas, n
    # āpta operacional = competence[dominio] modulada por stats (§5.4)

class Pramana(str, Enum):
    PRATYAKSHA = "pratyaksha"    # observación directa de herramienta
    SHABDA = "shabda"            # testimonio (contenido afirmado por una fuente)
    ANUMANA = "anumana"          # inferencia del modelo
    ARTHAPATTI = "arthapatti"    # abducción / mejor explicación
    UPAMANA = "upamana"          # analogía (opcional en v0.x)
    ANUPALABDHI = "anupalabdhi"  # ausencia, sujeta a yogyatā (R3)

class Status(str, Enum):
    IN = "in"                    # sostenida y usable
    OUT = "out"                  # derrotada o sin soporte
    PENDING = "pending"          # parataḥ: a la espera de verificación
    QUARANTINED = "quarantined"  # canal no confiable sin sanear

class Perishability(str, Enum):
    STABLE = "stable"   # matemáticas, APIs congeladas, historia
    SLOW = "slow"       # docs de librerías, organigramas
    FAST = "fast"       # versiones, precios, estados de servicio
    LIVE = "live"       # estado de runtime, ficheros, procesos

class Stakes(str, Enum):
    LOW = "low"; MED = "med"; HIGH = "high"; CRITICAL = "critical"

@dataclass
class EvidenceRef:
    evidence_id: str
    span: tuple[int, int] | None = None   # offsets sobre el payload

@dataclass
class Justification:
    id: str
    premises: list[str]          # belief_ids; deben estar IN para estar viva
    warrant: str                 # la vyāpti: regla general invocada, en LN
    audit: "ChainAudit | None"   # resultado del checklist trairūpya (Apéndice A)

@dataclass
class Belief:
    id: str
    content: str                       # proposición atómica, autocontenida (§2.2)
    pramana: Pramana
    source_id: str
    evidence: list[EvidenceRef]        # obligatorio para PRATYAKSHA/SHABDA
    justifications: list[Justification]  # obligatorio para ANUMANA/ARTHAPATTI
    qualifiers: dict[str, str]         # {"as_of": ..., "scope": ..., "assumes": ...}
    perishability: Perishability
    observed_at: datetime
    stakes: Stakes                     # heredado de la tarea; elevable por acción
    status: Status
    confidence: float | None = None    # auxiliar; NO gobierna bādha (R1)
    corroboration: int = 0             # nº de fuentes independientes concordantes (R6)

@dataclass
class DefeatEdge:
    id: str
    attacker: str                      # belief_id
    target: str                        # belief_id (REBUT) | justification_id (UNDERCUT)
    kind: str                          # "REBUT" | "UNDERCUT"
    basis: str                         # explicación en LN (auditoría)
    active: bool                       # recalculado por el motor (§4)

@dataclass
class VerificationTask:
    id: str
    belief_id: str
    method: str        # cross_source | tool_recheck | chain_audit | human
    k_required: int    # nº de confirmaciones independientes exigidas
    budget: int        # tokens/llamadas asignadas
    result: str | None # confirmed | disconfirmed | inconclusive
```

### 2.2 Normas de contenido de una creencia

El renderizado, la detección de contradicciones y el matching de claims dependen de que `content` sea disciplinado:

1. **Atómica:** una proposición por creencia. Si la ingesta produce una conjunción, se divide.
2. **Autocontenida:** sin pronombres ni deícticos («esta versión», «el fichero anterior»); entidades con nombre completo.
3. **Tiempo y ámbito explícitos** cuando aplique, en `qualifiers`, no embebidos en prosa ambigua.
4. **Longitud objetivo ≤ ~40 palabras.** Más largo suele indicar falta de atomicidad.
5. **Dedup:** hash de contenido normalizado; casi-duplicados de la *misma* raíz de procedencia se fusionan (no suman corroboración); de raíces independientes, incrementan `corroboration` (R6).

### 2.3 Esquema de persistencia (sketch)

```sql
CREATE TABLE events (              -- fuente de verdad, append-only (R8)
  seq INTEGER PRIMARY KEY,
  ts TEXT, kind TEXT,              -- INGESTED|TYPED|ADMITTED|DEFEATED|REINSTATED|
  payload JSON                     -- VERIFIED|RETRACTION_NOTICED|GATE_BLOCKED|SEARCH_FAILED
);
CREATE TABLE evidence  (id TEXT PRIMARY KEY, kind TEXT, payload_ref TEXT,
                        content_hash TEXT, meta JSON, ts TEXT);
CREATE TABLE sources   (id TEXT PRIMARY KEY, kind TEXT, integrity TEXT,
                        competence JSON, stats JSON);
CREATE TABLE beliefs   (id TEXT PRIMARY KEY, content TEXT, pramana TEXT,
                        source_id TEXT, qualifiers JSON, perishability TEXT,
                        observed_at TEXT, stakes TEXT, status TEXT,
                        confidence REAL, corroboration INTEGER);
CREATE TABLE belief_evidence (belief_id TEXT, evidence_id TEXT, span JSON);
CREATE TABLE justifications  (id TEXT PRIMARY KEY, belief_id TEXT,
                              warrant TEXT, audit JSON);
CREATE TABLE justification_premises (justification_id TEXT, premise_belief_id TEXT);
CREATE TABLE defeats   (id TEXT PRIMARY KEY, attacker TEXT, target TEXT,
                        kind TEXT, basis TEXT, active INTEGER);
CREATE TABLE verification_tasks (id TEXT PRIMARY KEY, belief_id TEXT, method TEXT,
                                 k_required INTEGER, budget INTEGER, result TEXT);
```

Índice vectorial opcional sobre `beliefs.content` para la selección de relevancia del compilador (§6.1). SQLite basta para v0.x; el grafo cabe en memoria por episodio (cientos a pocos miles de nodos).

---

## 3. Registro de tipos: condiciones de validez y derrotadores típicos

| Tipo | Qué lo produce | Condición de validez en ingesta | Derrotadores típicos |
|---|---|---|---|
| **PRATYAKSHA** | Salida de tool ejecutado por el harness | Tool terminó OK; salida parseada; la creencia cubre solo lo medido (R2); entorno íntegro | UNDERCUT: tool mal invocado, entorno corrupto, flakiness demostrada. REBUT: re-observación posterior en hechos FAST/LIVE |
| **SHABDA** | Contenido afirmado por doc/web/usuario/otro agente | Cita obligatoria a span de evidencia; fuente con āpta calculable; canal marcado (Integrity) | REBUT: pratyakṣa o testimonio de mayor āpta. UNDERCUT: fuente desacreditada en el dominio, doc obsoleto, contexto satírico/no asertivo |
| **ANUMANA** | Conclusión derivada por el modelo | Premisas listadas y todas IN; warrant (vyāpti) explícito; audit opcional (Apéndice A) según stakes | UNDERCUT: hetvābhāsa detectado en la cadena, premisa cae a OUT. REBUT: creencia de mayor prioridad contradictoria |
| **ARTHAPATTI** | Postulación abductiva («solo se explica si…») | El explanandum está IN; alternativas consideradas y descartadas quedan registradas | UNDERCUT: aparece una alternativa viable (derrotador constitutivo del tipo) |
| **UPAMANA** | Analogía («la API B se comporta como la A») | Base de similitud explícita; marcada siempre como la prioridad más baja | UNDERCUT: desanalogía relevante señalada. *Opcional en v0.x: puede modelarse como anumāna con warrant analógico* |
| **ANUPALABDHI** | Búsqueda negativa | **yogyatā** (R3): cobertura(clase_query, corpus) ≥ θ y recall_est ≥ θ′, parámetros de búsqueda registrados | REBUT: cualquier hallazgo positivo posterior (gana siempre). UNDERCUT: se demuestra cobertura insuficiente |

Reglas transversales:

- **Usuario:** afirmaciones sobre sí mismo o sus preferencias → śabda con āpta alto por defecto; afirmaciones sobre el mundo → śabda con āpta del dominio.
- **Otro agente/LLM:** siempre śabda con fuente = ese modelo/componente; nunca hereda el tipo de las fuentes que ese agente dice haber usado (a menos que exporte su propio ledger firmado, extensión futura).
- **Ledger previo (memoria):** reentra con tipo original + `qualifiers.as_of` + descuento por perecibilidad (R4). Hechos LIVE nunca reentran como IN: se re-observan.
- **vikalpa no es un tipo:** es el veredicto del linter sobre spans de salida sin creencia IN que los respalde (§7.3).

---

## 4. Motor de derrota (bādha)

### 4.1 Semántica

Dos clases de ataque (Pollock):

- **REBUT** (`attacker ⟂ target`): las proposiciones se contradicen tras normalizar qualifiers (R7). Ataca la *creencia*.
- **UNDERCUT**: ataca una *justificación* o la validez de ingesta de una creencia básica (el vínculo evidencia→creencia), sin afirmar la negación. Es la forma computable de las teorías khyāti: explica cómo una cognición convincente surge de un proceso defectuoso.

Detección de REBUT: bloqueo por vecindad (mismo cluster de entidad/tema vía embeddings) para evitar O(n²), después check de contradicción NLI/LLM sobre los pares candidatos, después reconciliación de qualifiers. Solo los pares que sobreviven generan DefeatEdge.

### 4.2 Orden de prioridad

```
priority(b) = ( integrity_rank(source(b)),      # trusted=2 > semi=1 > untrusted=0
                type_rank(b.pramana, dominio),   # tabla configurable, ver YAML
                reliability(b),                  # āpta efectivo o calidad de cadena, discretizado
                specificity(b),                  # específico > general (lex specialis)
                recency_rank(b) )                # solo pesa si perishability ∈ {FAST, LIVE}
```

Comparación lexicográfica. Un REBUT es **ganador** si `priority(attacker) > priority(target)` estrictamente en el primer componente que difiera. Igualdad o incomparabilidad configurada → **saṃśaya**: ninguna derrota a la otra; ambas quedan marcadas CONFLICT, se renderizan como conflicto abierto (§6.2) y se emite una VerificationTask. Los conflictos no se resuelven en silencio: la duda dispara indagación, no un tie-break arbitrario.

`type_rank` es dependiente de dominio y se declara en configuración:

```yaml
type_rank:
  default:            {pratyaksha: 5, shabda_apta_hi: 4, anumana_audited: 4,
                       shabda_apta_mid: 3, anumana_raw: 2, arthapatti: 2,
                       upamana: 1, shabda_apta_lo: 1}
domain_profiles:
  runtime_state:      {pratyaksha: 9}     # lo observado en el sistema manda
  library_internals:  {shabda_official_docs: 6}  # la doc oficial > inferencia local
reglas_fijas:
  - "hallazgo positivo > anupalabdhi, siempre"
  - "QUARANTINED/untrusted nunca derrota a trusted, sea cual sea el tipo"
```

### 4.3 Reetiquetado (punto fijo, estilo JTMS)

El grafo de justificaciones se fuerza acíclico en escritura (se rechaza el ciclo y se pide reformular). Las aristas de derrota sí pueden formar ciclos; se tratan con la regla saṃśaya.

```python
def relabel(ledger):
    # 0) normaliza qualifiers; recalcula pares REBUT vigentes (R7)
    # 1) inicialización: básicas con validez de tipo OK -> candidatas IN;
    #    derivadas -> desconocido
    # 2) itera hasta punto fijo:
    #    viva(j)      = all(status(p) == IN for p in j.premises) \
    #                   and not undercut_activo(j)
    #    soporte(b)   = es_basica_valida(b) or any(viva(j) for j in b.justifications)
    #    rebut_win(b) = exists a: rebuts(a, b) and status(a) == IN \
    #                   and priority(a) > priority(b)
    #    status(b)    = IN  si soporte(b) and not rebut_win(b)
    #                   OUT si not soporte(b) or  rebut_win(b)
    # 3) derrotas mutuas irresueltas (a⟂b, prioridades empatadas o ciclo impar):
    #    ambas -> PENDING + VerificationTask(saṃśaya)
    # 4) emite eventos por cada transición (retractaciones §4.4, āpta §5.4)
```

Terminación: retículo finito + regla PENDING para ciclos; en la práctica, tope de iteraciones con alarma. La **reinstauración** es gratuita: si el atacante cae a OUT en una pasada posterior, el objetivo recupera IN en el mismo punto fijo.

### 4.4 Protocolo de retractación

La derrota no basta: el modelo ya pudo haber usado la creencia. Cuando una creencia previamente *renderizada en contexto* transita IN→OUT:

1. Se encola `RetractionNotice(belief, causa, descendientes_afectados)`.
2. El compilador la renderiza en el bloque RETRACTACIONES (§6.2) durante los siguientes turnos, hasta que el modelo produzca salida que no dependa de ella (verificado por el linter) o expire un TTL.
3. Los descendientes (anumāna que la usaban como premisa) caen por propagación y se listan junto a ella: la retractación es del subárbol, no del nodo.

Este protocolo es el corazón del sistema: convierte «el modelo se corrige si se acuerda» en «el harness garantiza que la corrección llega y se propaga».

---

## 5. Política de confianza (prāmāṇya)

### 5.1 Modos

- **svataḥ** — admitir como IN inmediatamente; derrotable (validez intrínseca con defeaters).
- **parataḥ(k, método)** — entra como PENDING; pasa a IN tras k confirmaciones por el método indicado (validez que requiere certificación externa).
- **quarantine** — no entra al grafo activo; visible solo en auditoría.

### 5.2 Matriz por fuente × stakes (defaults; configuración YAML)

| Fuente \ Stakes | LOW | MED | HIGH | CRITICAL |
|---|---|---|---|---|
| pratyakṣa (tool propio) | svataḥ | svataḥ | svataḥ | parataḥ(1, re-observación) |
| śabda interno TRUSTED | svataḥ | svataḥ | parataḥ(1) | parataḥ(2) |
| śabda web SEMI | svataḥ | parataḥ(1) | parataḥ(2, indep.) | parataḥ(2, indep. + tool) |
| śabda web UNTRUSTED | svataḥ* | parataḥ(1) | parataḥ(2, indep.) | quarantine hasta corroborar |
| usuario (sobre sí) | svataḥ | svataḥ | svataḥ | confirmar en chat |
| usuario (sobre el mundo) | svataḥ | svataḥ | parataḥ(1) | parataḥ(2) |
| anumāna (cadena registrada) | svataḥ | svataḥ | parataḥ(chain_audit) | parataḥ(audit + tool_recheck) |
| anupalabdhi | yogyatā | yogyatā | yogyatā + re-búsqueda | no admitir: exigir positivo |

\* svataḥ pero con `integrity_rank = 0`: usable para tareas baratas, incapaz de derrotar nada trusted.

Los **stakes** los declara la tarea (default MED) y los **eleva la acción**: si una creencia es precondición de una acción HIGH (§7.4), su celda efectiva es la de HIGH aunque la tarea fuera LOW.

### 5.3 Verificadores

- `cross_source`: k testimonios independientes según R6 (raíces de procedencia distintas + dedup semántico).
- `tool_recheck`: convertir śabda en pratyakṣa cuando exista un observable («la doc dice X → ejecútalo y mira»). Es la verificación preferente: sube de tipo, no solo de contador.
- `chain_audit`: checklist trairūpya + linter hetvābhāsa sobre la justificación (Apéndice A).
- `human`: escalada; bloquea en CRITICAL si no hay respuesta.

**Presupuesto:** cada episodio lleva un budget de verificación (llamadas/tokens). Los PENDING que exceden presupuesto permanecen PENDING; el compilador los renderiza con qualifier explícito («según F, sin verificar») y el linter permite citarlos solo con ese marcador. Degradación honesta, no silenciosa.

### 5.4 Aprendizaje de āpta

Bucle lento sobre `Source.stats`: cada derrota confirmada de una creencia de la fuente decrementa su competencia efectiva en el dominio; cada confirmación independiente la incrementa (con suavizado tipo Beta y suelo/techo). Es la parte «parataḥ-aprāmāṇya aprendida»: la fiabilidad se gana y se pierde con historial, no se declara una vez. Los ajustes son eventos auditables como todo lo demás.

---

## 6. Compilador de contexto

El ledger es inerte si el prompt no lo hace respetar. El compilador es el producto real.

### 6.1 Selección

1. Recupera creencias relevantes al paso actual (índice vectorial sobre `content` + expansión de grafo: si entra una anumāna, entran los ids de sus premisas).
2. Ordena por (obligatorios, prioridad, relevancia) bajo presupuesto de tokens. Obligatorios: retractaciones vivas > conflictos abiertos > precondiciones de la acción en curso > resto.
3. PENDING solo se renderiza si es directamente relevante, siempre con su marcador.

### 6.2 Renderizado (gramática de línea)

```
[<id>][<tipo>][<meta>] <content> <qualifiers>

tipo:  P = pratyakṣa · Ś = śabda · A = anumāna · Ap = arthāpatti · ¬∃ = anupalabdhi
meta:  P  -> tool y timestamp          Ś  -> fuente y ā=āpta efectivo
       A  -> ← premisas [+audit✓]      ¬∃ -> yogyatā✓(θ) y query
```

Ejemplo de bloque compilado:

```
### LEDGER — creencias activas relevantes
[b41][P][pip index versions foo · 14:02] foo: última versión estable = 2.4.1
[b17][Ś][foo.dev ā=0.6] foo 2.x requiere Python >= 3.10  {as_of: 2026-05} (SIN VERIFICAR)
[b52][A ← b41,b09 · audit✓][warrant: semver, cambios de patch no rompen API pública]
      la firma de foo.bar() es idéntica entre 2.4.0 y 2.4.1
[b60][¬∃][yogyatā✓ 0.92 · grep -rn "legacy_mode" src/] no existe uso de legacy_mode en src/

### CONFLICTOS ABIERTOS (saṃśaya)
b17 ⟂ b33 (README interno dice Python >= 3.9) — verificación vt-7 en curso. No asumas ninguna.

### RETRACTACIONES
b12 «foo: última versión estable = 2.3» — DERROTADA por b41 (pratyakṣa > śabda web).
Cae también b29 (plan de pin a 2.3, derivada de b12). Corrige cualquier paso que dependa de ellas.

### CONTRATO DE GENERACIÓN
- Toda afirmación fáctica de tu salida debe citar [b·] o ir precedida de "especulación:".
- Prohibido citar creencias OUT o QUARANTINED; las PENDING solo con "(sin verificar)".
- Si necesitas un hecho que no está en el ledger, dilo y propón cómo obtenerlo (tool/búsqueda).
```

### 6.3 Compresión

En episodios largos, los resúmenes del ledger preservan el tipado (se resume por subgrafos, manteniendo ids y tipos de los nodos raíz). Colapsar a prosa destruye exactamente la estructura que justifica el sistema; está prohibido por diseño.

---

## 7. Ingesta, linting y gate de acciones

### 7.1 Ingesta de resultados de tool

Cada `ToolResult` produce: un EvidenceObject inmutable (hash, meta), una creencia pratyakṣa de wrapper (siempre), y cero o más creencias śabda de contenido si el payload contiene aserciones (R2). El extractor de aserciones es un LLM barato con prompt de extracción y las normas §2.2; su coste se controla extrayendo bajo demanda (lazy): el contenido queda indexado como evidencia y solo se promueve a creencias śabda cuando la selección de relevancia lo toca.

Contenido de canal UNTRUSTED: las aserciones se tipan con `integrity=untrusted`; cualquier texto con forma de instrucción no se ingiere como creencia ni como comando (capa anti-inyección del harness, fuera de alcance pero asumida).

### 7.2 Ingesta de mensajes de usuario y de generaciones propias

- Usuario: claims extraídos lazy con las reglas transversales de §3.
- Generación del modelo: las conclusiones que el modelo declara («por tanto X») se registran como anumāna *solo si* el contrato se cumplió (premisas citadas). Si no, son candidatas a vikalpa (§7.3), no creencias.

### 7.3 Linter (vikalpa)

Sobre la salida final del turno (y opcionalmente sobre acciones intermedias en HIGH+):

1. Extrae afirmaciones fácticas declarativas de la salida.
2. Matching contra el ledger: entailment de cada afirmación por alguna creencia IN (o PENDING si lleva el marcador).
3. Clasifica: **grounded** (cita válida) · **inferible** (se registra la anumāna que faltaba, con premisas) · **vikalpa** (sin respaldo).
4. Política por stakes: LOW → anotar y seguir; MED → reescritura con marcador de especulación o búsqueda de grounding; HIGH+ → bloquear la salida hasta resolver.

El linter es contenido (R5): sus veredictos se registran con fuente = linter, y su precisión se mide contra un set etiquetado (§10). Sin esa medición, el linter es un oráculo no auditado, exactamente lo que este sistema existe para eliminar.

### 7.4 Gate de acciones (precondiciones)

Antes de ejecutar una acción con efectos (stakes HIGH/CRITICAL declarados en el schema del tool):

```python
def gate_action(action) -> "ALLOW | ASK | BLOCK":
    for p in preconditions(action):      # declaradas en el tool schema o inferidas
        b = ledger.entails(p)
        if b is None or b.status != Status.IN or priority(b) < min_priority(action.stakes):
            return ASK(missing=p, sugerencia=cómo_obtenerla(p))
    return ALLOW
```

Es el punto de intervención barato: entre la evaluación y el compromiso, antes de que el efecto se propague. Las precondiciones típicas («el fichero existe», «el usuario confirmó», «el entorno es staging») son exactamente la clase de creencia que este sistema mantiene bien.

---

## 8. Integración en el harness

### 8.1 Interfaz (Protocol)

```python
class LedgerMiddleware(Protocol):
    def on_user_message(self, msg: Message) -> None: ...
    def on_tool_result(self, call: ToolCall, result: ToolResult) -> None: ...
    def compile_context(self, state: TaskState, budget_tokens: int) -> str: ...
    def on_model_output(self, text: str, actions: list[ToolCall]) -> LintReport: ...
    def gate_action(self, action: ToolCall) -> GateDecision: ...

# workers asíncronos:
#   verifier_worker(queue[VerificationTask])   -> ejecuta §5.3, emite VERIFIED
#   apta_updater(events)                        -> §5.4
#   relabel se invoca tras cada mutación del grafo (es barato: subgrafo afectado)
```

### 8.2 Adaptador para tu harness

No asumo internals de Hermes; el adaptador son ~50 líneas si el harness expone (a) wrapper de tool-calls, (b) middleware de mensajes, (c) hook o wrapper del cliente de modelo para pre/post-generación. Si falta (c), se envuelve el cliente. El compilador inyecta su bloque como mensaje de sistema efímero por turno (no acumulativo: se regenera, el histórico vive en el ledger, no en el transcript).

Orden de llamada por turno:

```
user_msg → on_user_message → compile_context → LLM → on_model_output
  → [gate_action → tool → on_tool_result → relabel]* → compile_context → LLM → ...
```

---

## 9. Traza de ejemplo (end-to-end)

Tarea (stakes MED): «actualiza requirements y el código para la última versión de foo».

1. Retriever devuelve un post de blog: `e1`. Ingesta: `b12 = [Ś][blog.example ā=0.5] «última estable de foo = 2.3» {as_of: 2025-11}`. Matriz §5.2 (web SEMI × MED) → parataḥ(1)… el presupuesto lo permite → vt-1 (tool_recheck) encolada; mientras, PENDING.
2. El modelo, con b12 renderizada como PENDING, propone provisionalmente `b29 = [A ← b12] «pin foo==2.3»` — el compilador la admite citada con «(sin verificar)».
3. vt-1 ejecuta `pip index versions foo` → `e2`, `b41 = [P] «última estable de foo = 2.4.1»`. Detector de contradicción: b41 ⟂ b12 (qualifiers reconciliados: ambas pretenden valer *ahora*; b12 además es FAST y vieja). priority(b41) > priority(b12) en type_rank → REBUT ganador.
4. `relabel`: b12 → OUT; b29 pierde su única justificación viva → OUT. Eventos DEFEATED×2; RetractionNotice(b12, {b29}). `apta_updater`: blog.example pierde competencia en `python_packaging`.
5. Turno siguiente: el compilador renderiza b41 IN, y RETRACTACIONES con b12+b29. El modelo corrige el plan citando [b41]. El linter confirma que la salida ya no depende de b12 → la notice expira.
6. Antes de escribir requirements (acción HIGH por schema): gate exige «existe requirements.txt en el repo» → no hay creencia → ASK → el harness ejecuta `ls`, crea la pratyakṣa, ALLOW.

Lo que compra el sistema en esta traza: la corrección no dependió de que el modelo «se acordara» del blog; fue estructural, propagada al descendiente, auditada, y dejó a la fuente con la reputación tocada.

---

## 10. Evaluación y criterios de colapso

### Suites

- **A — Grounding QA:** QA sensible al tiempo y multi-hop con distractores. Métricas: tasa de vikalpa en respuestas finales (grader independiente), precisión/cobertura de citación, calibración de los marcadores «sin verificar».
- **B — Sondas bādha (la suite propia):** episodios sintéticos con llegada programada de evidencia contradictoria y ground truth de qué debe ganar. Métricas: retraction rate, completitud de propagación (% de descendientes reetiquetados), wrong-winner rate, turnos-hasta-retractar. Casi no existe benchmark público de revisión de creencias en agentes; esta suite es en sí un artefacto publicable.
- **C — Tareas agénticas con fallos inyectados:** docs obsoletas vs. estado de runtime, ausencias con y sin yogyatā. Métricas: task success, tasa de acciones inseguras bloqueadas por el gate, falsos bloqueos.
- **D — El linter mismo (R5):** precisión/recall del detector de vikalpa contra set etiquetado a mano (~300 claims). Sin D, A no es interpretable.

### Ablations

flat baseline · solo tipos (sin derrota) · solo derrota (sin tipos) · sin contrato en el compilador · sin gate. La pregunta de cada ablation: ¿qué componente paga su coste?

### Costes y colapso

Registrar overhead de tokens y llamadas por configuración. Criterio de colapso, heredado del documento original: si Suite A no mejora de forma material (propuesta provisional: ≥15% relativo en vikalpa rate) con overhead aceptable (≤ +35% tokens en MED), colapsar a contexto plano y quedarse solo con lo que las ablations salven. Los números son placeholders a calibrar en v0.2; el compromiso de tener criterio de abandono no lo es.

---

## 11. Roadmap

- **v0.1 (1–2 semanas):** schema + eventos + compilador con renderizado y contrato; ingesta manual/semiautomática; derrota manual. Objetivo: falsar barato la hipótesis mínima — ¿el renderizado tipado por sí solo mueve Suite A?
- **v0.2 (+2–3 semanas):** ingesta automática con regla wrapper/contenido; detección de contradicciones (blocking + NLI); bādha con prioridades fijas; protocolo de retractación; Suite B v1.
- **v0.3 (+3–4 semanas):** linter vikalpa sobre salidas finales; verificadores parataḥ con presupuesto; aprendizaje de āpta; Suite D.
- **v1.0:** gate de acciones con precondiciones en tool schemas; suites completas + ablations; congelar spec y decidir colapso o continuación.

## 12. Problemas abiertos y riesgos honestos

1. **Granularidad de claims.** La atomicidad es difusa; sobre-atomizar explota el grafo, sub-atomizar rompe la derrota selectiva. Mitigación: normas §2.2 + presupuesto de creencias por episodio; es empírico.
2. **El extractor y el linter son LLMs.** Sus errores generan falsas alarmas de vikalpa (fatiga) o falsos grounded (peor). Por eso Suite D es prerequisito de cualquier conclusión, y por eso R5 no es decorativa.
3. **Adherencia al contrato.** Que el modelo cite [b·] disciplinadamente varía por modelo y es prompt-engineering empírico. El fallback es honesto: si no cita, el linter trata la afirmación como vikalpa y aplica política.
4. **Coste.** Extracción + NLI + verificación pueden suponer 1.5–3× llamadas si se hace todo eager. Mitigaciones ya en spec: lazy extraction, blocking, lint solo de salidas finales fuera de HIGH, presupuestos explícitos.
5. **Prioridades a mano.** El orden §4.2 es config inicial razonable, no verdad. Aprenderlo de los resultados de las sondas bādha (qué orden minimiza wrong-winner) es la extensión natural.
6. **Bayesianización.** Variante futura: estados blandos con umbrales. Se pospone deliberadamente (R1); si las sondas muestran que la lexicografía discreta pierde información decisiva, es el primer sitio donde ceder.
7. **Multi-agente.** Intercambio de subgrafos firmados entre ledgers (testimonio con cadena adjunta) queda fuera de v0.x, pero el tipado ya lo deja preparado: otro agente es una fuente śabda con āpta propio.

---

## Apéndice A — Auditoría de cadenas (para `chain_audit`)

**Checklist trairūpya** sobre la justificación (warrant + premisas):

1. *pakṣadharmatā* — la razón aplica de verdad al caso presente (las premisas mencionan este caso, no uno parecido).
2. *sapakṣe sattvam* — existe al menos una instancia positiva del warrant (ejemplo concreto registrable; la tradición exigía el udāharaṇa en el propio silogismo).
3. *vipakṣe asattvam* — una búsqueda rápida de contraejemplo falla (n intentos del propio modelo o del crítico).

**Taxonomía hetvābhāsa como categorías de lint** (conecta con el proyecto nº2, el linter de cadenas):

| Categoría | Lectura moderna | Acción del motor |
|---|---|---|
| savyabhicāra (inconcluyente) | el warrant admite contraejemplos conocidos | UNDERCUT a la justificación |
| viruddha (contradictoria) | el warrant, bien aplicado, soporta la negación | UNDERCUT + alerta |
| satpratipakṣa (contrabalanceada) | existe cadena opuesta de prioridad igual | marcar CONFLICT (saṃśaya) |
| asiddha (premisa no establecida) | alguna premisa no está IN | la propagación estándar ya lo cubre |
| bādhita (derrotada por superior) | conclusión contradicha por creencia de mayor prioridad | es literalmente el REBUT de §4; el linter solo lo etiqueta |

## Apéndice B — Trazabilidad concepto → componente

| Concepto | Componente de la spec |
|---|---|
| pramāṇa (tipología de fuentes) | enum `Pramana` + registro §3 |
| āpta (fuente competente y honesta) | `Source.competence/integrity` + aprendizaje §5.4 |
| vyāpti / trairūpya | `Justification.warrant` + `chain_audit` (Ap. A) |
| anupalabdhi + yogyatā | tipo ANUPALABDHI + R3 |
| bādha / khyāti | motor §4: REBUT / UNDERCUT |
| svataḥ- vs parataḥ-prāmāṇya | matriz §5.2 |
| saṃśaya (la duda dispara indagación) | CONFLICT → VerificationTask §4.2 |
| vikalpa | veredicto del linter §7.3 |
| smṛti no es pramāṇa | R4 (memoria como transporte) |
| vedanā como punto de intervención | gate de acciones §7.4 |
| el testigo no es privilegiado | R5 (monitor como contenido) + Suite D |

*Fin de la especificación v0.1-draft.*
