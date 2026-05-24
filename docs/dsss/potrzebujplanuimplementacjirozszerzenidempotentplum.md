# Plan implementacji: DSSS Directional Mesh Communication

> Plan przekazywany do Claude Code (zna codebase). Opisuje architekturę,
> strukturę plików, sekwencję iteracji i decyzje projektowe. **Nie zawiera
> kodu implementacyjnego** — sygnatury/protokoły poniżej to artefakty
> projektowe (kontrakty międzymodułowe), nie gotowy kod.

---

## Context — po co to robimy

rfmesh po pivocie ADR-021 jest **kierunkowym radiem comms-first**: te same
węzły, które robią DF dla geolokalizacji emiterów (efekt uboczny), mają
nawiązywać kierunkowe łącza między sobą. ADR-019 (rendezvous) już buduje
*pointing* anteny węzeł→węzeł, ale **nie ma żadnej warstwy nadawczej ani
modulacji** — system jest dziś wyłącznie RX/DF. Ta funkcja dokłada brakujący
kawałek: **łącze danych DSSS (BPSK, ~10 Mchip/s, spreading 1023, PG ~30 dB)
po kierunkowych beamach Yagi, z multi-hop routingiem przez mesh**, dające
LPI/LPD i geometryczne omijanie jammingu. Wpisuje się wprost w ADR-021/019 —
realizuje "primary product", którego te ADR-y dotąd tylko się domagały.

Stan wejściowy (potwierdzony eksploracją): kontrakty zamrożone na
`SCHEMA_VERSION = "1.1.0"`; **zero TX** w całym repo; **brak prymitywów DSSS**
(PN/LFSR, korelacja, ramki); ale pointing/servo (`rendezvous.py`,
`servo_motion.py`, `ServoDriver`) i symulator (`SyntheticReceiver`) są
dojrzałe i bezpośrednio reużywalne. Firmware (ESP32-C6) obsługuje tylko servo
— **DSSS jest w 100% host-side**, firmware nietknięty (B6).

Decyzje projektowe potwierdzone z leadem:
1. **Sprzęt:** celujemy w realny multi-node TX — zakładamy dołożenie SDR
   zdolnych do nadawania (HackRF One / Pluto+ potrafią TX) obok jednego
   BladeRF 2.0 micro. RTL-SDR pozostają RX-only (graceful degradation: węzeł
   RTL-SDR nie może być uczestnikiem comms, co musi padać głośno — B3).
2. **Kontrakty:** protokół `Transmitter` promujemy **do zamrożonych
   contracts teraz** (ADR-022 + bump `SCHEMA_VERSION` 1.1.0 → 1.2.0). Symetria
   z `Receiver`.
3. **Pakiet:** nowy, czysty pakiet `rfmesh-dsss` (izolowany side-project,
   lustrzane wzorce `rfmesh-dsp`).

Praca na osobnym branchu (`feature/directional-comms`), **bez merge do main do
pełnej walidacji**. Tryby DF i comms **wzajemnie wykluczające się** w tej
fazie (wybór flagą CLI / configiem), współistnienie w przyszłej iteracji.

---

## 1. Architecture overview — jak comms wpisuje się w rfmesh

Zachowujemy **star topology** (każdy pakiet importuje tylko z
`rfmesh-contracts`; `rfmesh-node` to composition root importujący wszystkich
sąsiadów) oraz **simulator-first** (cały protokół walidowany na syntetycznym
TX/RX/kanale zanim dotknie sprzętu — ARCHITECTURE §4).

```
            rfmesh-contracts  (1.2.0: +Transmitter/CoherentTransmitter,
                  ▲            +Capability.COMMS_DSSS, +CommsConfig)
                  │ (wszyscy importują tylko stąd)
   ┌──────────────┼───────────────┬───────────────┬──────────────┐
   │              │               │               │              │
rfmesh-sdr   rfmesh-dsss(NEW)  rfmesh-dsp     rfmesh-fusion   ...(bez zmian)
 +TX driver   czysty DSP DSSS  (DF, bez zmian)  (bez zmian)
 +Synthetic   PN/mod/korelacja
  TX+loopback /ramki/link-budget
   │              │
   └──────┬───────┘
          ▼
     rfmesh-node  (composition root)
     +comms/ : CommsLoop (pointing→TDD→TX/RX), mesh_routing, comms_config
     +wybór trybu DF vs COMMS (mutually exclusive)
     reuse: rendezvous.expected_servo_angle, servo_motion.move_smooth, ServoDriver
```

**Warstwy danych (mapowanie do istniejących wzorców):**

| Istniejący tor DF | Nowy tor COMMS | Reuse |
|---|---|---|
| `Receiver.read()` → IQ | `Transmitter.write()` ← IQ (NOWE, symetryczne) | wzorzec protokołu |
| L1/L2 estymator IQ→`BearingReport` | DSSS demod IQ→ramka/payload | wzorzec klasy + golden testy |
| `SyntheticReceiver` (sim) | `SyntheticTransmitter` + `loopback_channel` (sim) | modele kanału two_ray/multipath_fir |
| `RendezvousLoop` (pointing peer) | `CommsLoop` (pointing peer + TDD TX/RX) | `expected_servo_angle`, `move_smooth` |
| `Bearer` (node→fusion) | `mesh_routing` (node→node, multi-hop) | analogiczny, ale peer-to-peer |
| `BearingReport`/`FixEvent` (wire) | ramka DSSS (node-layer, NIE kontrakt) | — |

**Co NIE zmienia się:** tor DF (L1/L2/fusion/CoT/ops), bo tryby są wzajemnie
wykluczające. Comms to osobny pionowy wycinek; jedyne wspólne punkty to
contracts (protokół TX, capability, config), servo/pointing i symulator.

**Honesty (kultura B2/B3/B4):** comms nie emituje `BearingReport`, więc B2
(azimuth_sigma) nie stosuje się wprost — ale metryki łącza (BER, RSSI,
zmierzony processing gain) muszą być **mierzone empirycznie**, nigdy magiczne
stałe; brak łącza/synchro = głośna porażka (B3), nie cicha. Pointing dziedziczy
uczciwą sigmę z reużytego estymatora L1 (rendezvous).

---

## 2. Module breakdown — struktura plików

### 2a. `packages/rfmesh-contracts/` — ZMIANA (B1, ADR-022, bump 1.2.0)

Zmiana zamrożonych kontraktów. **Tylko lead, tylko po akceptacji ADR.** Bump
jest MINOR/additive (nowy protokół, nowy member enuma, nowy schemat config) —
precedens mechaniki: ADR-008 (1.0.0→1.1.0).

- `version.py`: `SCHEMA_VERSION = "1.2.0"`; `SchemaVersionT = Literal["1.2.0"]`.
  Blast radius: testy asertujące `"1.1.0"` trzeba zaktualizować (to jest
  zamierzony tripwire — mypy wskaże miejsca).
- `enums.py`: dodać do `Capability`:
  `COMMS_DSSS = "comms_dsss"` — węzeł uczestniczy w meshu DSSS (wymaga TX+RX).
  RX-only HW deklarujący to → fatal przy starcie (B3).
- `protocols.py`: dodać **symetrycznie do `Receiver`/`CoherentReceiver`**:
  - `TransmitterCapabilities` (Protocol): `driver`, `n_tx_channels`,
    `actual_sample_rate_hz`, `max_tx_power_normalized` (bez dBm — B.2).
  - `Transmitter` (`@runtime_checkable` Protocol):
    `open()`, `configure(NodeConfig)`, `write(iq: IQBlock) -> int` (zwraca liczbę
    wysłanych próbek lub podnosi — bez cichych short-write, B3),
    `capabilities() -> TransmitterCapabilities`, `close()`.
  - `CoherentTransmitter(Transmitter)`: `write_coherent(iq: CoherentIQBlock) -> int`.
  - Reuse aliasów `IQBlock`/`CoherentIQBlock` (TX konsumuje ten sam typ).
- `config.py`: dodać `CommsConfig` (`frozen=True, extra="forbid"`) — parametry
  **cross-workstream** (dotyczą dsss DSP + sdr + node):
  `carrier_freq_hz`, `chip_rate_hz`, `spreading_factor` (=1023),
  `lfsr_taps`/`lfsr_seed` (wybór m-sekwencji), `tdd_slot_ms`,
  `tdd_guard_ms`, `frame_payload_max_bytes`. Walidatory: `spreading_factor`
  spójny z długością m-sekwencji (2^n−1), `chip_rate_hz ≤ sample_rate_hz`.
  **NIE** dotykamy `NodeConfig` (peer roster/routing zostają node-layer — patrz 2d).

> **Bramka B1:** Iteracja 0 kończy się ADR-022 (PROPOSED) + diffem kontraktu.
> Implementujący agent **zatrzymuje się** i czeka na akceptację leada +
> bump `SCHEMA_VERSION` (B1 krok 3–4). Iteracje 1+ ruszają dopiero po akceptacji.

### 2b. `packages/rfmesh-dsss/` — NOWY, czysty pakiet (lustro `rfmesh-dsp`)

Czysty DSP (B5: bez network/file-IO/subprocess/SDR). Wzorce: czyste funkcje +
ewentualne klasy-estymatory, extensywne docstringi z wyprowadzeniem
matematycznym, golden testy w `tests/golden/`.

```
packages/rfmesh-dsss/
├── pyproject.toml                 # dep: rfmesh-contracts, numpy, scipy
├── README.md
├── src/rfmesh_dsss/
│   ├── __init__.py
│   ├── constants.py               # EPSILON, domyślne taps/seed, długości
│   ├── exceptions.py              # DsssError
│   ├── pn_sequence.py             # m-sekwencja LFSR len-10 (1023 chip); własności autokorelacji
│   ├── modulation.py              # BPSK map/demap (bity↔±1), opcjonalny pulse-shape
│   ├── spreading.py               # spread (bit→1023 chip), despread (korelacja z PN)
│   ├── correlation.py             # matched filter, FFT-correlation, akwizycja preambuły, code-phase search
│   ├── timing.py                  # chip/symbol timing recovery (early-late), korekta fazy/CFO
│   ├── framing.py                 # ramka: preamble+sync word+header(src/dst/seq/len)+payload+CRC; encode/decode; CRC-16 (własny, bez importu sibling)
│   ├── link_budget.py             # processing gain, teoretyczny BER(Eb/N0), uczciwe metryki łącza
│   └── py.typed
└── tests/
    ├── conftest.py                # fixtures: PN, kanał, scenariusze SNR
    ├── golden/                     # *.npz: PN, autokorelacja, spread↔despread, ramka roundtrip
    ├── test_pn_sequence.py
    ├── test_spreading.py
    ├── test_correlation.py
    ├── test_framing.py
    └── test_ber_honesty.py        # Monte-Carlo BER vs SNR (analog sigma-honesty)
```

### 2c. `packages/rfmesh-sdr/` — ROZSZERZENIE (warstwa TX, greenfield)

```
src/rfmesh_sdr/devices/
│   ├── bladerf.py                 # BladeRFCoherentReceiver + BladeRFTransmitter (native libbladeRF; fallback SoapySDR)
│   └── (opcjonalnie) hackrf.py / pluto.py — TX-capable, jeśli dochodzą jako nadajniki
└── simulator/
    ├── synthetic_transmitter.py   # SyntheticTransmitter / CoherentTransmitter (Protocol z contracts)
    └── loopback_channel.py        # in-process kanał TX→RX: path loss/delay/noise; reuse modeli two_ray, multipath_fir
```

`SyntheticTransmitter` zapisuje IQ do współdzielonego `loopback_channel`, z
którego czyta jeden/wiele `SyntheticReceiver` — dwa symulowane węzły wymieniają
ramki DSSS w pytest, bez sprzętu. To backbone simulator-first dla comms.

### 2d. `packages/rfmesh-node/` — ROZSZERZENIE (orkiestracja trybu COMMS)

Wzorzec dokładnie jak `rendezvous.py` — **node-layer, bez zmiany kontraktu**
dla rzeczy deployment/orchestration (peer roster, routing, TDD timing).

```
src/rfmesh_node/comms/
│   ├── __init__.py
│   ├── comms_config.py            # node-layer (jak RendezvousConfig): PeerEntry, PeerTable, RoutingTable, CommsLinkConfig
│   ├── mesh_routing.py            # RoutingTable z YAML; next_hop(dest)->node_id; logika relay; głośny brak trasy (B3)
│   ├── tdd.py                     # scheduler slotów TDD half-duplex (guard intervals)
│   └── comms_loop.py              # CommsLoop: pointing peer (reuse expected_servo_angle) → TDD TX/RX → send/recv ramek (dsss+transmitter+receiver+servo)
├── node.py                        # MODYFIKACJA: wybór trybu DF vs COMMS; build comms pipeline; tor DF nietknięty
└── cli/run_node.py                # MODYFIKACJA: flaga --mode {df,comms}; ładowanie comms YAML
```

Reuse wprost: `rendezvous.expected_servo_angle`, `rendezvous.geodesic_initial_bearing_deg`,
`servo_motion.move_smooth`, `ServoDriver.move/position`.

### 2e. Config / scenariusze / ADR / import-linter

- `configs/node-comms-*.yaml` — węzły w trybie comms (driver `bladerf`/`hackrf`/`pluto` lub `sim`).
- `scenarios/three_node_dsss_link.yaml` — A-B-C liniowo; A i C poza zasięgiem direct; C w zasięgu A i B; routing A↔C przez B.
- `docs/adr/ADR-022-dsss-directional-mesh-comms.md` — PROPOSED (wg szablonu ADR-021/019).
- `pyproject.toml` (root): dodać `rfmesh_dsss` do `root_packages`, do kontraktu
  **star independence**, oraz nowy kontrakt **purity** (forbid
  `requests/httpx/aiohttp/socket/subprocess` w `rfmesh_dsss`).

---

## 3. Sekwencja iteracji (każda niezależnie testowalna i mergeable do brancha)

**Iteracja 0 — ADR-022 + zmiana kontraktu (BRAMKA B1).**
ADR-022 (PROPOSED): framing comms-first, mutual-exclusivity trybów, dlaczego
TX→contracts, dlaczego pure-Python (sekcja 4), peer roster/routing node-layer.
Diff kontraktu: `Transmitter`/`CoherentTransmitter`/`TransmitterCapabilities`,
`Capability.COMMS_DSSS`, `CommsConfig`, bump `SCHEMA_VERSION`→1.2.0, aktualizacja
`SchemaVersionT` i testów pinujących wersję. Skeleton `rfmesh-dsss` (pyproject,
src, tests, README), rejestracja w workspace + import-linter (star + purity).
**Gate:** `uv sync`; `just lint` (lint-imports zielony); `just type`; testy
contracts (w tym tripwire) zielone. **Stop → akceptacja leada → lead bumpuje.**

**Iteracja 1 — Rdzeń DSSS DSP (pure, sim-only).**
`pn_sequence` (m-sekwencja len-10 = 1023 chip; weryfikacja długości, balansu,
2-poziomowej autokorelacji), `modulation` (BPSK), `spreading` (spread/despread),
processing gain. **Gate:** golden testy (.npz): znana m-sekwencja, pik
autokorelacji, roundtrip spread→despread odzyskuje bity przy wysokim SNR; `just verify`.

**Iteracja 2 — Akwizycja, synchronizacja, ramki.**
`framing` (preamble+sync+header+payload+CRC), `correlation` (matched filter /
FFT-correlation, detekcja startu ramki w szumie, code-phase), `timing`
(odzysk taktu chipów). **Gate:** golden roundtrip ramki; Monte-Carlo
`test_ber_honesty` — prawdopodobieństwo detekcji ramki i BER vs SNR mieszczą
się w paśmie przewidzianym przez processing gain (analog sigma-honesty, ±20%);
`just verify`.

**Iteracja 3 — Syntetyczny TX + kanał loopback (rfmesh-sdr).**
Implementacje `Transmitter`/`CoherentTransmitter` w sdr; `SyntheticTransmitter`
+ `loopback_channel` (reuse modeli kanału). **Gate:** test integracyjny
end-to-end: modulate ramkę → kanał (path loss/multipath/noise) → demodulate →
odzyskany payload, przy kilku SNR; `just verify`. To domyka simulator-first.

**Iteracja 4 — Tryb COMMS w node (pojedyncze łącze).**
`comms_config`, `comms_loop` (pointing peer reuse → TDD TX/RX → send/recv),
`tdd`; wybór trybu w `node.py` + `--mode comms` w CLI (tor DF nietknięty).
**Gate:** test integracyjny: dwa syntetyczne węzły przez loopback wymieniają
wiadomość tekstową z pointingiem servo; unit testy wyboru trybu i walidacji
comms YAML (`extra="forbid"`); `just verify`.

**Iteracja 5 — Multi-hop static routing + relay.**
`mesh_routing` (RoutingTable z YAML, `next_hop`, relay), scenariusz 3-węzłowy
A-B-C. **Gate:** test integracyjny — A→C przez relay B (weryfikacja liczby
hopów, pointingu na każdym hopie, integralności payloadu CRC); test degradacji:
usunięcie B zrywa A↔C z głośną porażką (B3); `just verify`.

**Iteracja 6 — Driver BladeRF (TX+RX) + (opc.) HackRF/Pluto TX; test sprzętowy.**
`devices/bladerf.py` (+ ew. hackrf/pluto), native libbladeRF lub SoapySDR.
**Gate:** `@pytest.mark.hardware` smoke (loopback kablowy/eter), nigdy w CI;
config-swap `sim`→`bladerf` bez zmiany kodu DSP. Software i tak shipuje na
symulatorze, gdyby sprzęt nie dojechał (postawa jak L2/Pluto w ARCHITECTURE §5).

**Iteracja 7 (opcjonalna) — wizualizacja łącza w ops** (link ribbons, RSSI,
BER), zgodnie z `link.html` z ADR-021. Poza rdzeniem; follow-up.

---

## 4. Decyzja: framework — **Pure Python + numpy/scipy** (NIE GNU Radio)

**Rekomendacja:** cały DSP w czystym Pythonie (numpy/scipy) w `rfmesh-dsss`;
I/O sprzętowe przez **SoapySDR** (ścieżka generyczna) + **native libbladeRF**
(coherent). GNU Radio odrzucone, hybryda odrzucona.

**Argumentacja:**
1. **Purity (B5) + import-linter.** rfmesh-dsp/fusion zakazują
   `subprocess`/`socket`. GNU Radio to runtime flowgraphów (scheduler C++,
   własne wątki, gr-blocks) — paradygmat I/O+runtime, sprzeczny z modelem
   czystych funkcji i kontraktem purity. Czysty `rfmesh-dsss` zachowuje go.
2. **Simulator-first (ARCHITECTURE §4).** Cały model dev to "DSP działa na
   syntetycznym IQ bez zmiany kodu". Czyste funkcje numpy testują się
   deterministycznie golden-plikami `.npz` (jak L1/L2); flowgraphy GNU Radio
   nie wpinają się w ten wzorzec.
3. **Waga zależności + uv.** GNU Radio to ciężki pakiet systemowy (apt/conda),
   nie instaluje się czysto przez pip/uv; obciążyłby każdy obraz RPi i byłby
   bolesnym `uvadd-request`. numpy/scipy już są zależnościami.
4. **Spójność z konwencją.** Repo = czysty numpy/scipy + golden testy. Nowy
   kod ma pasować do wzorców (wymóg). Pure-Python pasuje; GNU Radio to obcy
   paradygmat.
5. **Przepustowość.** Ciężka część (korelacja/despread) wektoryzuje się
   `scipy.signal.fftconvolve`/FFT — wystarczy do block-processing i golden
   testów. Realtime 10 Msps na sprzęcie to kwestia buforowania SoapySDR; jeśli
   hot-loop będzie wąskim gardłem, eskalacja to **numba** (lekki, uv-friendly),
   nie GNU Radio. Net rate POC ~10 kbit/s — skromny.

**Świadomy trade-off:** pure-Python = więcej kodu od zera (akwizycja, timing
recovery) i ostrożne buforowanie realtime. To właściwy koszt za zgodność
architektoniczną. Hybryda odrzucona, bo i tak ciągnie runtime+zależność GNU
Radio i rozbija DSP na dwa paradygmaty, niszcząc determinizm golden testów.

---

## 5. Testing strategy (proporcjonalna do krytyczności)

| Komponent | Krytyczność | Strategia |
|---|---|---|
| DSSS DSP (PN, spread/despread, ramki) | wysoka, deterministyczna | **golden `.npz`** (wzorzec rfmesh-dsp): znana m-sekwencja, autokorelacja, roundtrip; generator jednorazowy `golden_generator.py` |
| Akwizycja/synchro/BER | wysoka | **Monte-Carlo `test_ber_honesty`** vs SNR {10,20,30} dB, ±20% pasmo wokół predykcji z processing gain (analog sigma-honesty); reuse modeli multipath symulatora |
| Loopback end-to-end | wysoka | integracja: modulate→kanał→demodulate→payload przy wielu SNR |
| Orkiestracja node (CommsLoop, TDD) | średnia | async integracja dwóch syntetycznych węzłów; unit wyboru trybu; pointing pokryty istniejącymi testami rendezvous |
| Routing/relay | średnia | unit `next_hop`/relay; integracja 3-węzłowa A→C przez B; test degradacji (B3) |
| Config comms | średnia | walidacja Pydantic (`extra="forbid"`, walidatory spreading/chip-rate) |
| Driver BladeRF/HackRF/Pluto | (sprzętowa) | `@pytest.mark.hardware`, opt-in, nigdy w CI |

Zasada: nie 100% coverage za wszelką cenę — testujemy critical paths i edge
cases (niski SNR, brak synchro, brak trasy, peer poza łukiem ±90°, HW RX-only
deklarujący COMMS_DSSS).

---

## 6. Risk assessment (najtrudniejsze komponenty)

1. **Dostępność TX na sprzęcie (najwyższe ryzyko logistyczne).** Lead zakłada
   dołożenie nadajników (HackRF/Pluto). Jeśli nie dojadą — multi-node TX na
   sprzęcie się nie zmaterializuje. *Mitigacja:* simulator-first waliduje pełny
   protokół niezależnie od sprzętu; software shipuje na symulatorze; HW jako
   config-swap. RTL-SDR jako RX-only obsłużone głośną porażką (B3).
2. **Realtime 10 Mchip/s w Pythonie.** Per-sample za wolne. *Mitigacja:*
   block-vectorized DSP; profilowanie; numba dla hot-loop jeśli trzeba
   (`uvadd-request`); POC net rate skromny.
3. **Akwizycja/timing recovery w niskim SNR + multipath.** Najtrudniejszy DSP.
   *Mitigacja:* Monte-Carlo vs istniejące modele kanału (two_ray, multipath_fir);
   golden testy; PG 30 dB daje zapas.
4. **Koordynacja TDD przy luźnym synchro (~10 ms NTP).** *Mitigacja:* hojne
   guard intervals; statyczny harmonogram slotów w configu; skromny bitrate
   toleruje luźny timing.
5. **Greenfield driver BladeRF (sprzęt pożyczony, niepewny).** *Mitigacja:*
   najpierw SoapySDR generic; native libbladeRF tylko gdy konieczne; testy
   sprzętowe gated; sim odblokowuje resztę.
6. **Pokusa rozlania zmian kontraktu (B1).** *Mitigacja:* TX→contracts robimy
   raz, kontrolowanym ADR-022; peer roster/routing/TDD zostają node-layer
   (precedens RendezvousConfig). Żadnych dalszych edycji frozen-contracts bez
   kolejnego ADR.
7. **Honesty metryk łącza (kultura B2/B4).** *Mitigacja:* BER/RSSI/PG mierzone
   empirycznie, Monte-Carlo walidowane; brak magicznych stałych; pointing
   dziedziczy uczciwą sigmę estymatora L1.

---

## 7. Weryfikacja end-to-end (jak sprawdzić, że działa)

- **Per-iteracja:** `just verify` (ruff + mypy strict + pytest "not hardware" +
  lint-imports) zielone; wkleić wynix przy zamykaniu iteracji (protokół §5 AGENTS).
- **Golden:** `uv run python packages/rfmesh-dsss/tests/golden_generator.py`
  regeneruje deterministycznie; testy golden zielone.
- **Symulacja łącza (iter 3–5):**
  `uv run pytest packages/rfmesh-dsss packages/rfmesh-node -k comms` — pełny
  multi-hop A→B→C na syntetycznym TX/RX/kanale, payload odzyskany, hop count
  i pointing zgodne, degradacja przy usunięciu B głośna.
- **Sprzęt (iter 6, opt-in):** `uv run pytest -m hardware` na stanowisku z
  BladeRF; config-swap `sim`→`bladerf`.
- **Council review przed ewentualnym merge do main** (CLAUDE.md): Architect
  (B1 — czy zmiana kontraktu poprawna i przez ADR? czy COMMS_DSSS spójne?),
  Code Reviewer (purity rfmesh-dsss, brak cichych fallbacków, golden obecne),
  RF-DSP Specialist (poprawność DSSS, akwizycja, BER honesty), Demo Integrity
  (czytelność łącza w ops, uczciwość metryk). 4× APPROVE przed merge.

---

## Załącznik: pliki krytyczne do modyfikacji

- Kontrakt (lead, ADR-022): `packages/rfmesh-contracts/src/rfmesh_contracts/{version,enums,protocols,config}.py`
- Nowy pakiet: `packages/rfmesh-dsss/**` (lustro `packages/rfmesh-dsp/`)
- TX SDR: `packages/rfmesh-sdr/src/rfmesh_sdr/devices/bladerf.py`,
  `packages/rfmesh-sdr/src/rfmesh_sdr/simulator/{synthetic_transmitter,loopback_channel}.py`
- Node comms: `packages/rfmesh-node/src/rfmesh_node/comms/**`,
  modyfikacje `node.py` i `cli/run_node.py`
  (reuse `rendezvous.py`, `servo_motion.py`)
- Root: `pyproject.toml` (workspace member + import-linter star/purity)
- Docs/config: `docs/adr/ADR-022-dsss-directional-mesh-comms.md`,
  `scenarios/three_node_dsss_link.yaml`, `configs/node-comms-*.yaml`
</content>
</invoke>
