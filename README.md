# PivotCut

Desktop app di animazione 2D cut-out per macOS (Apple Silicon), ispirata a
Pivot Animator. Personaggi composti da parti PNG trasparenti riutilizzabili,
timeline a miniature, layer con parallasse 2.5D, export PNG sequence e MP4.

Stato attuale: **Milestone 5B — Packaging macOS locale (Apple Silicon)**.
Import PNG, asset registry, rig cut-out gerarchico a cinematica diretta
(forward kinematics), layer di background/foreground con parallasse 2.5D
basata su profondità, camera virtuale per frame, inspector per layer e
camera, playback della timeline che rispetta FPS/exposure, export di una
sequenza PNG e export MP4 H.264 via FFmpeg, miniature timeline renderizzate
realmente dal contenuto scena, riordino frame via drag-and-drop, Undo/Redo
affidabile per tutte le operazioni di editing, un processo ripetibile per
costruire PivotCut come `.app` locale via PyInstaller, e una guida in-app
cliccabile (menu Help -> Getting Started…, più testi "What's This?" su
praticamente ogni pulsante/campo/pannello, richiamabili dal pulsante "?"
in toolbar o con Shift+F1) pensata per chi apre l'app per la prima volta.

## Prerequisiti

- macOS su Apple Silicon
- Python 3.12 o superiore (verificato con `python3.12 --version`)
- FFmpeg **opzionale**: necessario solo per l'export MP4. Senza FFmpeg
  installato l'app resta pienamente funzionante — playback ed export PNG
  sequence non richiedono FFmpeg in alcun modo. Per installarlo:
  `brew install ffmpeg`.

## Installazione

```bash
cd /Users/alessio/Progetti/PivotCut
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## Avvio dell'applicazione

```bash
source .venv/bin/activate
PYTHONPATH=src python -m pivotcut.main
```

## Esecuzione dei test

```bash
source .venv/bin/activate
python -m pytest
```

`pyproject.toml` imposta `pythonpath = ["src"]` per pytest, quindi non serve
installare il pacchetto in modalità editable per eseguire i test.

## Build macOS (Apple Silicon)

Produce un bundle `PivotCut.app` **locale, non firmato**, tramite
PyInstaller — pensato per l'uso sulla propria macchina, non ancora per la
distribuzione (firma, notarizzazione e DMG sono previsti separatamente,
Milestone 5C).

### Prerequisiti

- macOS su Apple Silicon.
- Un interprete Python **arm64 nativo** (non x86_64 sotto Rosetta): lo
  script di build lo verifica ed esce con errore altrimenti. Controllo
  manuale:

  ```bash
  python3 -c "import platform; print(platform.machine())"
  # deve stampare: arm64
  ```

- Ambiente virtuale attivato con tutte le dipendenze installate (PyInstaller
  incluso — è nel `requirements.txt`/negli extra `dev` di `pyproject.toml`):

  ```bash
  source .venv/bin/activate
  pip install -r requirements.txt
  ```

### Comando di build

```bash
./scripts/build_macos.sh
# oppure: bash scripts/build_macos.sh
```

Lo script, in ordine:

1. verifica di essere su macOS ed esce altrimenti;
2. verifica che l'interprete Python sia arm64 nativo;
3. verifica che PyInstaller sia installato;
4. ripulisce `build/`/`dist/` preesistenti (con conferma interattiva, o
   automaticamente con `--clean`);
5. esegue l'intera suite `pytest` e **interrompe la build se un test
   fallisce**;
6. invoca `python -m PyInstaller pivotcut.spec`;
7. verifica che `dist/PivotCut.app` esista davvero, poi ne stampa il path.

Opzioni: `--clean` (pulizia automatica senza prompt), `--skip-tests` (salta
la gate dei test — solo per iterare sul packaging stesso, mai per una build
da distribuire). `scripts/clean_build.sh` rimuove solo `build/`/`dist/`,
per un cleanup indipendente dalla build.

### Dove trovare l'app e come avviarla

Il bundle finito è in `dist/PivotCut.app`. Due modi di avviarlo:

- **Da Finder**: doppio click su `dist/PivotCut.app` (prima apertura: vedi
  Gatekeeper sotto).
- **Da terminale**: `open dist/PivotCut.app`, oppure direttamente
  l'eseguibile interno (utile per vedere subito eventuali errori):
  `dist/PivotCut.app/Contents/MacOS/PivotCut`.

### Problemi comuni

- **Python x86_64/Rosetta**: se `platform.machine()` stampa `x86_64` pur
  essendo su Apple Silicon, l'interprete attivo gira sotto Rosetta (spesso
  un venv creato con un Python Intel installato per errore). Ricrea il
  venv con un Python arm64 nativo (es. `python3.12 -m venv .venv` con un
  Python scaricato per Apple Silicon) prima di ricostruire.
- **Errori di plugin/piattaforma Qt** ("could not find or load the Qt
  platform plugin cocoa"): quasi sempre sintomo di una build fatta con un
  PySide6 non coerente con l'interprete usato (es. mescolando arch), o di
  una `dist/` "sporca" da un tentativo precedente — prova `./scripts/build_macos.sh --clean`.
- **Gatekeeper ("PivotCut.app è danneggiata/non può essere aperta")**:
  atteso per una build non firmata/non notarizzata. Click destro
  sull'app -> **Apri**, poi conferma nel dialogo — necessario solo alla
  prima apertura. Questo NON è un limite risolvibile in questa milestone:
  firma e notarizzazione sono Milestone 5C.
- **FFmpeg assente**: il bundle non include FFmpeg e non lo richiede per
  funzionare — l'export PNG Sequence funziona comunque; Export MP4 mostra
  lo stesso messaggio "ffmpeg non trovato" già presente da Milestone 4,
  con il suggerimento `brew install ffmpeg`.
- **App avviata da Finder con `PATH` diverso dal terminale**: su macOS le
  app avviate da Finder non ereditano il `PATH`/le variabili d'ambiente
  della shell (a differenza di quando si lancia l'eseguibile da terminale).
  PivotCut non dipende dal `PATH` per le proprie funzionalità core, ma
  `services/ffmpeg_export.find_ffmpeg()` cerca `ffmpeg` sia su `PATH` sia
  esplicitamente in `/opt/homebrew/bin/ffmpeg` (il prefisso Homebrew
  standard su Apple Silicon) proprio per restare affidabile anche quando
  l'app è lanciata da Finder e un FFmpeg installato solo via un `PATH` di
  shell personalizzato non sarebbe altrimenti visibile.

## Struttura del progetto

```
pivotcut/
  README.md
  requirements.txt
  pyproject.toml
  .gitignore
  pivotcut.spec                # configurazione PyInstaller (bundle .app macOS, Milestone 5B)
  scripts/
    build_macos.sh              # build ripetibile: check arm64, pytest gate, PyInstaller, verifica bundle
    clean_build.sh               # rimuove solo build/ e dist/
  src/
    pivotcut/
      __init__.py
      main.py                    # entry point applicazione
      runtime_paths.py            # resource_path()/package_root(): unico punto che ispeziona sys.frozen/_MEIPASS
      app/
        main_window.py           # MainWindow: toolbar, menu Characters, splitter asset panel + canvas/timeline
      domain/
        models.py                # SceneSettings, Frame, Project (dataclass, no Qt) + coordinate + rig_templates
        assets.py                 # Asset (dataclass pura, leaf module)
        rig.py                    # Bone, Rig, RigTemplate, Matrix2D, validazione, world transform, attach/pivot (no Qt)
        layer.py                  # Layer (dataclass pura), validate_layer(s), create_layer_from_asset
        camera.py                 # Camera, parallax_factor, screen_position, zoom, layer/character screen matrix
        timeline.py               # operazioni pure su Project: new/delete/move/select/reorder frame
        playback.py                # espansione pose->frame video, conteggio/durata output, validazione fps/exposure
        commands.py                 # Command pattern + UndoRedoStack, senza Qt (inclusi i comandi rig/template)
      services/
        asset_manager.py          # import PNG, dedup, path resolution (Pillow + un QFileDialog)
        project_io.py             # save/load JSON, eccezioni tipizzate (inclusi rig_templates)
        rig_template_io.py         # import/export .pivotcut-rig.json (template esterno condivisibile)
        export_renderer.py         # rendering headless Project/Frame -> QImage/PNG/thumbnail (no CanvasView/UI)
        ffmpeg_export.py           # rilevamento ffmpeg, subprocess, encoding H.264 MP4
        playback_controller.py     # QTimer-based playback (fps/exposure, loop, is_playing)
        thumbnail_cache.py          # cache miniature timeline, chiave (frame_id, revision, size, mode)
      ui/
        canvas_view.py            # QGraphicsScene/View: zoom, pan, selezione/drag rig, selezione layer
        graphics_items.py          # BoneItem/LayerItem, placeholder asset mancante, RigRenderer/LayerRenderer
        rig_builder_dialog.py       # Character Rig Builder: import PNG, gerarchia parti, pivot/attach, preview
        timeline_widget.py         # miniature renderizzate, drag-and-drop reorder, pulsanti, fps/exposure
        asset_panel.py             # pannello Assets: import, lista, "Quick Create Single-Part Rig"/"Add Layer"
        inspector_panel.py         # pannello Inspector: proprietà layer selezionato, o camera del frame
      resources/
  tests/
    test_timeline.py
    test_project_io.py
    test_rig_transforms.py
    test_rig_template.py            # dominio: attach/pivot, reparent/remove/duplicate, RigTemplate, istanziazione
    test_rig_template_io.py          # import/export .pivotcut-rig.json, asset mapping
    test_parallax.py
    test_playback.py
    test_export_renderer.py
    test_ffmpeg_export.py
    test_history.py                # UndoRedoStack + tutti i comandi (inclusi rig/template) + dirty state + MainWindow
    test_thumbnail_cache.py         # render_thumbnail + ThumbnailCache
    conftest.py                    # fixture QApplication condivisa per i test che toccano Qt
```

Il dominio (`domain/`) non importa mai Qt: è testabile e usabile senza avviare
la GUI. La UI (`ui/`, `app/`) chiama sempre le funzioni pure di `domain/timeline.py`,
`domain/rig.py` e `domain/camera.py` per garantire lo stesso comportamento
validato dai test.

`domain/assets.py` esiste come modulo a sé stante (invece di stare in
`models.py` o in `services/asset_manager.py`) per evitare un import circolare:
`models.py` importa `Rig` da `rig.py`, `Layer` da `layer.py`, `Camera` da
`camera.py` e `Asset` da `assets.py`; `rig.py` e `layer.py` importano `Asset`
da `assets.py` per validare i propri riferimenti; `camera.py` importa `Layer`
da `layer.py` (per calcolare la trasformazione schermo di un layer) e
`Matrix2D` da `rig.py` (riuso della stessa matematica affine, invece di
duplicarla). La direzione delle dipendenze resta sempre a senso unico:
`assets.py` ← `rig.py`/`layer.py` ← `camera.py` ← `models.py`. Se `Asset`
fosse stato dentro `models.py`, si sarebbe creato un ciclo. `services/asset_manager.py`
resta il punto d'ingresso per l'*import* di un PNG (Pillow + `QFileDialog`):
contiene la logica, non il dato.

## Funzionalità di questa milestone

- **Canvas**: `QGraphicsScene`/`QGraphicsView`, scena 1920x1080 di default,
  zoom con rotellina/trackpad, pan con tasto centrale del mouse o Space+drag.
- **Rig cut-out PNG**: import di PNG trasparenti, `AssetManager` con
  deduplica (reimportare lo stesso file riusa l'asset esistente), un rig per
  frame composto da bone gerarchici (forward kinematics), pivot per-immagine,
  z-index, visibilità e opacità.
- **Manipolazione base**: click su una parte per selezionarla (bounding box
  evidenziato), drag normale per spostarla (in local space rispetto al
  parent; scene space per il root), Shift+drag orizzontale per ruotarla
  attorno al proprio pivot.
- **Rendering**: ogni bone è un `QGraphicsPixmapItem` "flat" (senza
  `setParentItem` per la gerarchia): la trasformazione mondo di ogni bone è
  calcolata in Python puro (`domain.rig.world_transforms`) e applicata come
  `QTransform` diretto — il modello di dominio resta l'unica fonte di verità
  e la scena Qt può sempre essere ricostruita da zero senza perdita di dati.
  Un asset PNG mancante mostra un placeholder tratteggiato con etichetta,
  senza bloccare l'apertura del progetto.
- **Timeline**: striscia di miniature **renderizzate realmente** (160×90,
  aspect ratio della scena preservato con letterbox/pillarbox), frame
  selezionato evidenziato, riordino via **drag-and-drop** (indicatore chiaro
  del punto di inserimento) più i pulsanti Move Left/Move Right come
  fallback accessibile, pulsanti Previous/Next/New Frame/Delete Frame,
  controlli FPS ed Exposure a livello di progetto, ogni miniatura mostra
  "Pose N — label ×exposure". "New Frame" duplica anche i rig del frame
  corrente (deep copy: id di rig/bone invariati, ma modificare una posa nel
  nuovo frame non tocca quello sorgente). La timeline scrolla automaticamente
  per rendere visibile il frame corrente dopo ogni navigazione/riproduzione/
  Undo/Redo/duplicazione/cancellazione/riordino.
- **Undo/Redo**: `Cmd+Z`/`Cmd+Shift+Z` (convenzioni macOS/Qt standard),
  pulsanti e menu Edit con la descrizione dell'azione (es. "Undo Move Bone").
  Copre New/Delete/Move/Reorder Frame, Create Rig, Add Layer, drag di un bone
  (move o rotate — una sola entry per l'intero gesto, non per ogni pixel di
  movimento), modifiche layer/camera dall'Inspector (una entry per modifica
  completata — `editingFinished`/focus-out/toggle, non per ogni tick dello
  spinner), e Reset Camera. Eseguire un nuovo comando dopo un Undo elimina il
  ramo Redo; storico limitato a 100 comandi. Vedi "Undo/Redo" più sotto per i
  dettagli architetturali.
- **Dirty state**: il titolo finestra mostra `*` se ci sono modifiche non
  salvate; Save/Save As lo rimuove. New Project/Open/chiusura finestra
  chiedono conferma (Save/Discard/Cancel) se il progetto ha modifiche non
  salvate.
- **Shortcut**: `N` nuovo frame, `Backspace`/`Delete` cancella frame, frecce
  sinistra/destra navigazione, `Space` è il toggle globale Play/Stop (eccetto
  quando un campo di testo — `QLineEdit`/`QSpinBox`/`QDoubleSpinBox`/
  `QComboBox` — ha il focus, dove digita normalmente uno spazio). Se il focus
  è sul canvas, `Space` tenuto premuto attiva comunque il pan: uno
  spazio+drag pan non attiva/disattiva il playback, solo pressione/rilascio
  senza drag lo fa.
- **Playback**: Play/Stop dalla toolbar o con `Space`. Riparte dal frame
  selezionato, mantiene ogni posa per `exposure` tick, avanza alla posa
  successiva, si ferma automaticamente all'ultimo frame (comportamento MVP).
  Il pulsante "Loop" (toggle in toolbar) fa ripartire dal primo frame dopo
  l'ultimo invece di fermarsi. Durante la riproduzione la timeline e
  l'inspector sono bloccati (nessuna modifica distruttiva mentre gioca), il
  canvas resta visibile ma non modificabile; New/Open/Save e gli asset
  restano bloccati fino allo Stop. La status bar mostra in modo discreto
  "Pose X/N", "Video frame Y/totale", FPS, Exposure e la durata video stimata.
- **Export PNG Sequence…**: dalla toolbar, chiede una cartella di
  destinazione e scrive `frame_000001.png`, `frame_000002.png`, ... (un file
  per ogni frame video esposizione-espanso: `len(project.frames) *
  project.exposure` immagini totali). Se la cartella contiene già file
  `frame_*.png` chiede conferma prima di sovrascriverli. Annullabile: i file
  già scritti restano su disco, non vengono cancellati. Non richiede FFmpeg.
- **Export MP4…**: dalla toolbar, verifica FFmpeg (`PATH`, poi
  `/opt/homebrew/bin/ffmpeg` su Apple Silicon); se assente mostra un
  messaggio chiaro (mai un traceback) e suggerisce `brew install ffmpeg`
  lasciando comunque disponibile l'export PNG. Se presente, chiede il file
  `.mp4` di destinazione, renderizza la sequenza PNG esposizione-espansa in
  una cartella temporanea (sfondo forzato opaco, dato che yuv420p/H.264 non
  supporta l'alpha), poi invoca
  `ffmpeg -y -framerate {fps} -i frame_%06d.png -c:v libx264 -pix_fmt yuv420p
  -movflags +faststart -crf 18 {output}` via `subprocess.run` (nessuna
  shell). Verifica che il file di output esista e abbia dimensione > 0 prima
  di dichiarare successo; pulisce sempre la cartella temporanea, anche in
  caso di errore o annullamento.
- **Progresso ed annullamento**: un `QProgressDialog` (fase, barra di
  avanzamento, pulsante Cancel) segue sia il rendering PNG sia l'encoding
  MP4; l'interfaccia resta reattiva perché `QProgressDialog.setValue()`
  processa gli eventi Qt internamente tra un frame e l'altro — nessun
  blocco prolungato del thread principale, nessuna necessità di un
  `QThread` (che avrebbe rischiato di creare `QPixmap` fuori dal thread
  GUI, non supportato da Qt).
- **Progetto**: New Project, Open, Save, Save As. Formato `.pivotcut.json`
  versionato (`format_version`, invariato a 1: evoluzione retrocompatibile),
  con `scene_settings`, `fps`, `exposure`, `assets`, `frames` (ognuno con i
  propri `rigs`), `current_frame_index`. Gli asset mantengono `source_path`
  assoluto sempre valido e `relative_path` ricalcolato ad ogni salvataggio
  rispetto alla cartella del progetto.
- **Layer 2.5D**: layer di background/midground/foreground riutilizzando lo
  stesso `AssetManager`. Pannello Assets: bottone "Add Layer From Selected
  Asset" + selettore tipo (Background/Midground/Foreground), che imposta solo
  valori di default (`z_index`/`z_depth`), non un modello distinto — il layer
  resta liberamente modificabile dopo la creazione.
- **Camera virtuale e parallasse**: `Camera` (x, y, zoom) come snapshot per
  frame, esattamente come i rig. I layer più lontani (z_depth alto) si
  spostano meno quando la camera si sposta; il rig si muove sempre come il
  "piano personaggi" (z_depth = 0). Vedi formula sotto.
- **Inspector**: pannello a destra con i campi del layer selezionato, o della
  camera del frame corrente se non c'è selezione. Ogni modifica scrive
  direttamente nel frame corrente e risincronizza il canvas. Include il
  pulsante "Reset Camera" (azzera x/y/zoom del solo frame corrente).
- **Toolbar**: New, Open, Save, Undo, Redo, Play/Stop, Loop, New Frame,
  Delete Frame, Export PNG Sequence…, Export MP4… — tutti funzionanti.

## Character Rig Builder (Milestone 6A)

### Terminologia

- **Asset**: un PNG registrato nel progetto (`Project.assets`), riutilizzabile
  da qualunque numero di rig/layer.
- **Rig Template**: una struttura di personaggio riutilizzabile (gerarchia di
  parti, pivot, attach point, z-index) **senza alcuna posa di timeline**.
  Vive in `Project.rig_templates` (la "Rig Library") e non viene mai
  renderizzato/posato direttamente.
- **Rig Instance**: un rig effettivamente presente in un `Frame`
  (`Frame.rigs`), con una posa modificabile per-frame. Creare un personaggio
  dalla libreria (`instantiate_rig_template`) produce una Rig Instance del
  tutto indipendente dal template di origine (id di rig/bone rigenerati).
- **Bone/Part**: una singola parte PNG del rig. Il dominio usa "Bone"
  (coerenza con Milestone 2-5A); l'interfaccia del Rig Builder la chiama
  sempre "Part" per restare vicina al vocabolario di Pivot Animator.
- **Root Part**: l'unico bone senza `parent_id` in un rig/template.
- **Pivot**: il punto, in coordinate locali dell'immagine PNG, attorno a cui
  la parte ruota/scala (vedi `local_matrix`).
- **Attach Point**: il punto, in coordinate locali del **genitore**, dove il
  pivot di questa parte deve trovarsi — cioè "dove la parte si aggancia al
  genitore".
- **z_index**: solo ordine visivo di disegno tra le parti di uno stesso rig;
  non ha alcuna relazione con la profondità di parallasse (`z_depth`, che
  resta un concetto di `Layer`/`Camera`).

### Pivot vs Attach Point

Sono due concetti distinti che in questa milestone risultano sempre
**coincidenti nello spazio mondo per costruzione**: il pivot del figlio
"aggancia" esattamente l'attach point del genitore. Concretamente:

- **Pivot** risponde a "da dove ruota/scala questa parte?" — spostarlo
  ricentra la rotazione mantenendo l'attach point fisso (la parte si sposta
  visivamente per compensare).
- **Attach Point** risponde a "dove si collega questa parte al genitore?" —
  spostarlo muove l'intera parte (pivot compreso) mantenendo la geometria di
  rotazione/scala invariata.

Nel Rig Builder i due handle sono disegnati come **cerchi concentrici** nello
stesso punto (anello esterno ciano = attach point, punto pieno interno
giallo = pivot), con una legenda esplicita nel pannello di preview; sono
draggabili indipendentemente e i valori numerici esatti restano sempre
visibili/editabili nell'Inspector per precisione. Questi handle e le linee di
"scheletro" tra attach/pivot sono visibili **solo nel Builder**, mai nel
render finale/canvas principale/export.

Internamente `Bone.attach_x`/`attach_y` non entrano mai nella matematica di
`local_matrix`/`world_transforms` (invariata da Milestone 2): vengono
riconciliati in `x`/`y` tramite `set_attach_point`/
`set_pivot_preserving_attach_point`, così il posizionamento in scena normale
(drag di una posa sul canvas principale) resta l'esatto codice già validato
dalle milestone precedenti.

### Flusso di lavoro

1. **Characters → New Character Rig…** apre il Rig Builder: quattro colonne
   (asset importabili, gerarchia del rig, preview con handle pivot/attach,
   inspector della parte selezionata).
2. Importa uno o più PNG (singolo file, selezione multipla, o intera
   cartella) — riusa `AssetManager`, quindi un PNG già registrato non viene
   mai duplicato; il builder propone un nome leggibile dal filename (es.
   `upper_arm_L.png` -> "Upper arm L") senza imporre alcuna convenzione.
3. "Add Part": la prima parte aggiunta diventa automaticamente la root; le
   successive si agganciano alla parte attualmente selezionata (o alla root
   se nessuna è selezionata).
4. Ogni parte è modificabile nell'Inspector: nome, genitore (con Reparent
   protetto da validazione anti-ciclo), posizione locale, rotazione, scala,
   pivot, attach point, z-index, opacità, visibilità.
5. Strumenti non distruttivi (mai sovrascrivono il PNG sorgente): Fit to
   Canvas, Reset Scale, Set Pivot Center, Crop Preview to Alpha Bounds
   (ricentra il pivot sul bounding box alpha-visibile, calcolato con
   Pillow — salvato come metadato/offset del rig, non come nuovo file),
   dimensione di riferimento della scena.
6. "Save Rig" valida l'intera struttura (`validate_rig_template`: un solo
   root, nessun ciclo, asset esistenti, opacità/scale validi) prima di
   chiudere il dialogo; "Cancel" scarta tutto senza toccare `Project`,
   history o cache miniature.
7. **Characters → Save Selected Rig as Template…** cattura la Rig Instance
   selezionata come voce libreria (`Project.rig_templates`), indipendente
   dalle pose sui frame.
8. **Characters → Add Character From Library…** istanzia un template scelto
   come nuova Rig Instance nel frame corrente (id di rig/bone rigenerati,
   posizionata al centro scena).
9. **Characters → Export/Import Rig Template…** scrive/legge un file
   `.pivotcut-rig.json` standalone (mai binari PNG — solo riferimenti
   informativi id/nome/dimensioni/percorso indicativo). All'import, se
   mancano asset richiesti si apre una UI di mapping (associa a un asset
   esistente, oppure importa un PNG sostitutivo); un template con
   riferimenti irrisolti non viene mai inserito.
10. **Characters → Quick Create Single-Part Rig** preserva esattamente il
    comportamento a singolo PNG delle milestone precedenti — non è più
    l'unico modo di creare un personaggio, ma resta disponibile invariato.

### "Edit Selected Character Rig": strategia scelta

Modificare la struttura di un rig (Add/Remove/Reparent/Duplicate Part) tocca
**solo la Rig Instance del frame corrente** — mai propagato automaticamente
alle altre pose/frame che condividono lo stesso personaggio, e mai al
Rig Template di libreria. Aggiornare il template è un'azione separata ed
esplicita ("Save Selected Rig as Template…"). Questa scelta (raccomandata
esplicitamente per l'MVP) evita che una modifica strutturale rompa
silenziosamente pose già animate su altri frame; il prezzo è che sincronizzare
manualmente più istanze dello stesso personaggio resta a carico dell'utente
in questa milestone (vedi M6B più sotto).

### Undo/Redo, dirty state, cache miniature

L'intera sessione del Builder (molti edit locali su una copia temporanea di
bone) produce **un solo comando** alla chiusura con Save
(`EditRigStructureCommand` per un rig esistente, `CreateRigCommand` per un
rig nuovo) — mai una entry per singolo Add/Remove/Reparent. Salvare un
template (`SaveRigTemplateCommand`)/cancellarlo (`DeleteRigTemplateCommand`)
sono comandi undo/redo-abili a parte, che segnano il progetto come "dirty";
l'export di un template esterno non lo segna (nessun contenuto di `Project`
cambia). Dopo ogni comando la miniatura del frame interessato viene
invalidata (`ThumbnailCache.bump`) e canvas/timeline/inspector sono
risincronizzati, esattamente come ogni altra mutazione dalla Milestone 5A.
Tutte le azioni del menu Characters sono disabilitate durante
playback/export, come ogni altra azione mutante.

### Nessun limite "un rig per frame"

Da questa milestone `Frame.rigs` può contenere più di un rig: l'architettura
lo supportava già dalla Milestone 2 (`RigRenderer`/`export_renderer`
iteravano sempre tutti i rig di un frame), il limite era solo un vincolo
artificiale nell'interfaccia (`AssetPanel`), ora rimosso. Cliccare una parte
sul canvas seleziona anche il rig a cui appartiene
(`CanvasView.selected_rig_id`), usato per capire a quale rig si riferiscono
le azioni "Save as Template"/"Edit Selected Character Rig" quando un frame ne
contiene più di uno.

## Undo/Redo: architettura

`Project`/`Frame` restano l'unica fonte di verità: `domain/commands.py`
(senza Qt) definisce un `Command` Protocol (`description`, `apply(project)`,
`revert(project)`) e `UndoRedoStack` (`execute`/`undo`/`redo`/`can_undo`/
`can_redo`/`clear`, storico limitato a `DEFAULT_MAX_HISTORY = 100` via
`collections.deque(maxlen=...)`, eseguire un nuovo comando dopo un Undo
elimina il ramo Redo). Ogni azione della UI che deve essere annullabile
passa da `MainWindow._execute_command()`, mai da una mutazione diretta:

- **Comandi strutturali** (`NewFrameCommand`, `DeleteFrameCommand`) avvolgono
  le funzioni pure esistenti di `domain/timeline.py` e ne catturano
  pigramente l'effetto (id fresco, suffisso "(copy)", ...) alla *prima*
  `apply()`, così un Redo successivo rigioca esattamente lo stesso effetto
  invece di generare un nuovo id casuale.
- **`MoveFrameCommand`** avvolge `domain.timeline.move_frame(project,
  from_index, to_index)` — la stessa funzione generalizzata che ora serve
  sia Move Left/Right sia il riordino via drag-and-drop, garantendo
  comportamento identico ovunque.
- **`CreateRigCommand`/`AddLayerCommand`** aggiungono/rimuovono per id un
  `Rig`/`Layer` già costruito dalle factory pure di dominio.
- **Comandi a snapshot mirato** (`TransformBoneCommand`, `EditLayerCommand`,
  `EditCameraCommand`) salvano una *deep copy* del solo oggetto modificato
  (un `Bone`/`Layer`/`Camera`, mai l'intero `Project`) immediatamente prima
  e immediatamente dopo un gesto di editing completo — non per ogni
  pixel di drag o ogni tick di uno spinner:
  - `CanvasView` cattura lo snapshot "before" quando il drag di un bone
    inizia e ne emette uno solo (`bone_transform_committed`) al rilascio
    del mouse, se lo stato è realmente cambiato.
  - `InspectorPanel` cattura la baseline ad ogni cambio di frame/selezione
    e ricommitta (`layer_edit_committed`/`camera_edit_committed`) su
    `editingFinished` (spinbox/campo nome) o subito dopo un toggle
    checkbox/click su Reset Camera — mai su ogni `valueChanged` intermedio,
    che continua a scrivere live nel modello solo per l'anteprima canvas.
    Se il valore finale coincide con quello iniziale, nessun comando viene
    creato.
- Durante playback o export, `MainWindow._can_mutate_model()` blocca
  `_execute_command`/`_undo`/`_redo` (oltre alla disabilitazione visiva di
  timeline/inspector/toolbar), come difesa aggiuntiva contro la
  ri-entranza generata da `QProgressDialog.setValue()` che processa eventi
  Qt durante l'export.

## Miniature timeline: rendering e cache

`services/export_renderer.render_thumbnail(project, frame_index, max_width,
max_height)` riusa `render_frame_to_qimage()` (nessuna pipeline duplicata),
poi scala preservando l'aspect ratio della scena con letterbox/pillarbox su
una tela trasparente `max_width`×`max_height` — 160×90 di default. Nessun
overlay, bordo di output o griglia: stessa garanzia del rendering di export.

`services/thumbnail_cache.py::ThumbnailCache` mantiene una cache con chiave
`(frame_id, revision, max_width, max_height, background_mode)`. `Frame` non
ha alcun concetto di "revision" — è deliberatamente tenuto fuori dal
dominio Qt-free, come bookkeeping puramente derivato per l'invalidazione
della cache UI:

- `bump(frame_id)`: invalida solo le miniature di quel frame. Chiamato da
  `MainWindow._bump_thumbnail_for_command()` dopo ogni comando che tocca
  `frame_id` (bone/layer/camera/rig/layer creation).
- `bump_all()`: invalida tutta la cache. Usato per New/Delete Frame (id che
  cambiano) e per il caricamento di un intero progetto; costo trascurabile
  data la bassa frequenza di questi eventi.
- Il riordino (`MoveFrameCommand`) non invalida nulla: cambia solo
  l'ordine, mai il contenuto renderizzato di un frame.

Non esiste un timer di debounce dedicato: dato che un comando viene creato
solo al termine di un gesto di editing (drag-release, `editingFinished`),
l'invalidazione della cache è già naturalmente "debounced" dallo stesso
meccanismo che serve Undo/Redo — non è stata aggiunta un'infrastruttura
separata.

## Sistema di coordinate e parallasse

Documentato in `domain/models.py` (convenzioni scena/rig) e `domain/camera.py`
(formula di parallasse, con esempi):

- Origine scena: alto-sinistra. X positivo a destra, Y positivo in basso.
- Unità: pixel scena. Rotazioni in gradi, positive in senso orario.
- I valori di un bone (`x`/`y`/`rotation`/`scale_x`/`scale_y`) sono in local
  space rispetto al `parent_id`, tranne il bone root di un rig, che è in
  world/scene space. Un `Layer` (senza gerarchia) usa la stessa identica
  composizione del bone root: traslazione a `(x, y)` → rotazione attorno al
  pivot → scala attorno allo stesso pivot.
- **Formula di parallasse** (`domain/camera.py`):

  ```
  parallax_factor = 1.0 / (1.0 + z_depth)
  screen_position  = world_position - camera_position * parallax_factor
  ```

  - `z_depth = 0` → `factor = 1.0`: si muove come il piano personaggi (i bone
    del rig usano sempre questo caso).
  - `z_depth = 1` → `factor = 0.5`: si muove la metà (es. un layer midground).
  - `z_depth = 3` → `factor = 0.25`: si muove un quarto (es. un layer
    background lontano).
- `camera.zoom` (limitato a `[0.1, 5.0]`) scala l'intera scena renderizzata
  (rig e layer) attorno al centro del frame di output — non tocca lo zoom di
  navigazione dell'editor (vedi sotto).
- Nessuna prospettiva 3D, shader, rotazione camera o `z_depth` negativo nell'MVP.

## Camera di progetto vs zoom/pan dell'editor

Sono due concetti volutamente separati:

- **Zoom/pan di navigazione** (rotellina/trackpad, Space+drag, tasto centrale
  del mouse sul `CanvasView`): serve solo per lavorare comodamente sul canvas.
  Non è mai salvato nel progetto e non tocca il modello di dominio — chiama
  solo `QGraphicsView.scale()`/scrollbar.
- **Camera di progetto** (`Frame.camera`, editabile dall'Inspector): un valore
  di dominio, salvato per frame, che sposta/scala effettivamente la scena
  renderizzata (rig + layer) tramite la formula di parallasse sopra. È quello
  che determinerà cosa finisce nell'export finale (Milestone 4).

Una cornice tratteggiata bianca nel canvas segna sempre l'area di output
1920x1080 (o le dimensioni scena configurate): non si muove con la camera —
è rig e layer che si spostano "sotto" di essa, simulando il movimento camera.

## Come creare un layer

1. Importa un PNG dal pannello Assets ("Import PNG…").
2. Selezionalo nella lista.
3. Scegli il tipo (Background / Midground / Foreground) dal menu a tendina:
   imposta solo i valori iniziali di `z_index`/`z_depth` (-100/3.0, -50/1.0,
   100/0.0 rispettivamente) — modificabili liberamente dopo, dall'Inspector.
4. Clicca "Add Layer From Selected Asset": il layer viene centrato in scena,
   con pivot al centro dell'immagine, e aggiunto solo al frame corrente.
5. Clicca sul layer nel canvas per selezionarlo e modificarne le proprietà
   dall'Inspector (posizione, rotazione, scala, pivot, z-index, z-depth,
   opacità, visibilità).

## Playback ed export: pose della timeline vs frame video

Distinzione fondamentale, documentata in `domain/playback.py`:

- Una **posa della timeline** (`Project.frames[i]`) NON equivale a un
  **frame video** (un PNG della sequenza esportata, o un frame dell'MP4
  codificato). Ogni posa viene mantenuta per `Project.exposure` frame video
  consecutivi, a `Project.fps` frame video al secondo — nessun tweening/
  interpolazione tra pose in questa milestone: un frame video mostra sempre
  esattamente una posa, invariata, per tutta la sua finestra di esposizione.
- `total_output_frames = len(project.frames) * project.exposure`.
- Esempio guida (verificato sia nei test automatici sia manualmente): 4 pose,
  fps=24, exposure=3 → 12 frame video → durata 12/24 = 0.5s. La posa `i`
  (0-indicizzata) occupa i frame video 1-indicizzati da `i * exposure + 1` a
  `(i + 1) * exposure`.
- `domain/playback.py` espone tre funzioni pure testate:
  `expanded_timeline_indices(frame_count, exposure) -> list[int]`,
  `output_frame_count(frame_count, exposure) -> int`,
  `output_duration_seconds(frame_count, exposure, fps) -> float`, più
  `validate_playback_settings(fps, exposure, scene_width, scene_height)` che
  richiede tutti e quattro i valori interi positivi (usata sia al
  caricamento progetto in `project_io.py`, sia prima di ogni export).
- Il rendering di export (`services/export_renderer.py`) legge solo
  `Project`/`Frame`/asset e produce `QImage`: non dipende mai da
  `MainWindow`, `CanvasView`, selezione o zoom dell'editor. Riusa la stessa
  matematica di posizionamento di `RigRenderer`/`LayerRenderer`
  (`world_transforms` + `character_plane_matrix` per i bone,
  `layer_screen_matrix` per i layer) e gli stessi item Qt "senza stato"
  (`BoneItem`/`MissingAssetItem`/`LayerItem`/`MissingLayerItem`), ma tramite
  una pipeline dedicata che non aggiunge mai overlay di selezione, cornice
  di output o griglia — l'export contiene esclusivamente il contenuto finale
  della scena.
- Limitazioni esplicite di questa milestone: nessun tweening/interpolazione
  fra pose, nessun audio, nessun canale alpha nell'MP4 (yuv420p/H.264 non lo
  supporta: lo sfondo viene sempre reso opaco prima dell'encoding). La
  sequenza PNG invece preserva la trasparenza se `scene_settings.background_color`
  è configurato con alpha ridotto.

## Limiti espliciti della Milestone 6A

- Nessun tweening/interpolazione automatica tra pose (invariato — non è
  obiettivo di questa milestone).
- Nessuna inverse kinematics: solo forward kinematics, come dalla Milestone 2.
  Ruotare un braccio muove correttamente avambraccio/mano perché sono figli
  nella gerarchia, ma non esiste alcun solver IK (trascinare una mano non
  ricalcola automaticamente la rotazione del braccio).
- Nessun audio, nessuna modifica a packaging/firma/notarizzazione (invariati
  dalla Milestone 5B).
- "Crop Preview to Alpha Bounds" è semplificato a un ricentraggio del pivot
  sul bounding box alpha-visibile del PNG (metadato/offset del rig): non
  produce un vero e proprio ritaglio/rettangolo di crop persistente, né un
  nuovo file PNG.
- L'import di asset PNG (anche dall'interno del Rig Builder) non è tracciato
  da Undo/Redo, in continuità con il comportamento del pannello Assets dalla
  Milestone 1: annullare l'intera sessione del Builder con "Cancel" non
  rimuove eventuali asset importati durante la sessione (restano registrati
  nel progetto, semplicemente non referenziati da alcun rig se non salvati).
- "Edit Selected Character Rig" modifica solo la Rig Instance del frame
  corrente, mai propagata automaticamente ad altre pose dello stesso
  personaggio né al Rig Template di libreria (vedi sopra "Edit Selected
  Character Rig: strategia scelta"); sincronizzare più istanze richiede
  un'azione manuale esplicita ("Save as Template" + "Add Character From
  Library" sui frame da aggiornare).
- "Set Root" (re-root della gerarchia) è disponibile solo dentro il Builder,
  non come azione rapida sul canvas principale.
- Nessuna libreria di categorie/tag ricercabile per i Rig Template oltre al
  singolo campo testuale `category`; nessuna anteprima miniaturizzata dei
  template nella UI di selezione (solo elenco per nome).
- Il formato `.pivotcut-rig.json` non incorpora mai i PNG: riaprire un
  template esportato in un altro progetto richiede sempre di risolvere gli
  asset mancanti (mapping manuale o reimport), anche se i file PNG originali
  sono fisicamente disponibili in una cartella nota.

## Limiti espliciti della Milestone 5B

- ~~Un solo rig per frame nell'interfaccia~~ — limite rimosso in Milestone 6A
  (vedi "Character Rig Builder" sopra); nessun limite sul numero di layer per
  frame.
- I layer sono selezionabili (per l'Inspector) ma non trascinabili nel
  canvas in questa milestone: l'editing avviene tramite l'Inspector.
- Nessun tweening/interpolazione automatica tra pose: ogni frame video
  mostra esattamente una posa, invariata, per tutta la sua esposizione.
- Nessun audio nell'export MP4.
- Nessun canale alpha nell'MP4 (limite di yuv420p/H.264): lo sfondo viene
  sempre reso opaco prima dell'encoding. La sequenza PNG preserva invece la
  trasparenza configurata.
- L'export gira sul thread principale con un `QProgressDialog` che processa
  gli eventi Qt tra un frame e l'altro (nessun `QThread`, per evitare di
  creare `QPixmap`/`QGraphicsScene`/`QPainter` fuori dal thread GUI, non
  supportato da Qt); l'annullamento è garantito prima e durante il rendering
  PNG e prima della fase di encoding FFmpeg, non a metà di una chiamata
  `ffmpeg` già avviata (blocco tipicamente breve per animazioni di questa
  scala).
- Trascinare una miniatura durante playback/export non è impedito
  visivamente (il gesto di drag può iniziare), ma il drop viene sempre
  ignorato in modo sicuro — nessuna mutazione del modello può avvenire
  mentre `is_playing`/`is_exporting` è vero; è solo un piccolo limite di
  UX, non di correttezza/sicurezza.
- Nessuna selezione multipla, nessun handle grafico di trasformazione,
  nessuna inverse kinematics, nessuno snapping.
- Nessuna prospettiva 3D, blur, shader o rotazione della camera.
- Gli asset PNG non vengono copiati fisicamente nella cartella del progetto:
  solo `source_path`/`relative_path` sono tracciati.
- Copia/incolla pose tra frame non è ancora implementato (previsto ma non
  MVP, come da requisiti).
- `Frame` non ha un campo "revision": la cache miniature lo tiene
  deliberatamente fuori dal dominio (vedi "Miniature timeline" sopra), quindi
  una mutazione del modello che bypassa il sistema di comandi (possibile
  solo da codice, non dall'interfaccia) non invaliderebbe automaticamente la
  cache — ogni percorso UI reale passa sempre da un comando.
- Il bundle `.app` prodotto in questa milestone è **locale e non firmato**:
  nessuna firma Apple Developer, nessuna notarizzazione, nessun DMG — al
  primo avvio Gatekeeper richiede click destro -> Apri (vedi "Build macOS"
  sopra). Non è pensato per la distribuzione ad altri utenti.
- Target di build: solo **arm64 nativo** (Apple Silicon). Questo NON è un
  binario universal2: non gira nativamente su Mac Intel (né lo dichiara).
- FFmpeg non è mai incluso nel bundle e non è richiesto per l'export PNG;
  l'export MP4 senza FFmpeg installato mostra lo stesso messaggio "non
  trovato" già presente da Milestone 4.

## Changelog

### Milestone 6A — Character Rig Builder stile Pivot

- `domain/rig.py`: `Bone` esteso con `attach_x`/`attach_y` (coincidono per
  costruzione col pivot del figlio, senza toccare `local_matrix`/
  `world_transforms`, invariati da Milestone 2); nuovo `RigTemplate`
  (id/name/root_bone_id/bones + `canvas_width`/`canvas_height`/`category`
  opzionali); nuove funzioni pure `set_attach_point`/
  `set_pivot_preserving_attach_point`, `create_child_bone`, `reparent_bone`
  (con rifiuto di self-parent/cicli), `remove_bone_subtree`/
  `remove_bone_reparent_children`, `duplicate_bone`, `set_root_bone`
  (re-root per inversione del percorso), `rig_template_from_rig`/
  `instantiate_rig_template` (rigenerazione id rig/bone, remap
  genitore-figlio, riposizionamento della sola root); `validate_rig`/
  `validate_rig_template` condividono ora la stessa validazione strutturale.
- `domain/models.py`: `Project.rig_templates: list[RigTemplate]` (la Rig
  Library), persistito in `.pivotcut.json` con default retrocompatibile
  `[]` per progetti pre-6A.
- `services/project_io.py`: round-trip di `rig_templates`; bone di progetti
  legacy senza `attach_x`/`attach_y` li derivano in modo semanticamente
  corretto da `x`/`y`/`pivot_x`/`pivot_y` esistenti, non da zero.
- Nuovo `services/rig_template_io.py`: formato esterno standalone
  `.pivotcut-rig.json` (`format_version` + un `RigTemplate` + riferimenti
  asset informativi id/nome/dimensioni/percorso — mai binari PNG);
  `missing_asset_ids`/`remap_template_asset_ids` per il flusso di mapping
  asset all'import.
- `domain/commands.py`: nuovi `EditRigStructureCommand` (un solo comando
  atomico per l'intera sessione del Builder, opera sulla singola Rig
  Instance del frame, mai sull'intero `Project`), `SaveRigTemplateCommand`
  (create-or-update nella libreria), `DeleteRigTemplateCommand`.
- Nuovo `ui/rig_builder_dialog.py`: dialogo a quattro colonne (asset
  importabili, gerarchia, preview con handle pivot/attach draggabili,
  inspector) che lavora su una copia locale di bone — Cancel non tocca mai
  `Project`/history/cache miniature, Save produce esattamente un comando.
- `app/main_window.py`: nuovo menu **Characters** (New Character Rig…, Quick
  Create Single-Part Rig, Add Character From Library…, Save Selected Rig as
  Template…, Import/Export Rig Template…, Edit Selected Character Rig…);
  rimosso il limite artificiale "un rig per frame" nell'interfaccia
  (l'architettura lo supportava già dalla Milestone 2).
- Test: 27 nuovi test di dominio puro (`test_rig_template.py`: coincidenza
  pivot/attach su almeno tre livelli di gerarchia, reparent/cicli, rimozione
  sottoalbero, reparent-to-grandparent, `set_root_bone`, conversione
  Rig<->RigTemplate, validazione); 13 nuovi test per l'I/O del template
  esterno (`test_rig_template_io.py`); nuovi test in `test_history.py` per
  `EditRigStructureCommand`/`SaveRigTemplateCommand`/
  `DeleteRigTemplateCommand` (execute/undo/redo, una sola history entry per
  sessione Builder) e tre test di integrazione MainWindow (Cancel Builder non
  cambia `Project`, Save Builder invalida la miniatura del frame, modificare
  la posa di un frame non muta il template salvato). Suite completa: 270/270
  test passati.
- Nessuna regressione sul comportamento Milestone 1-5B.

### Milestone 5B — Packaging macOS locale (Apple Silicon)
- Nuovo `pivotcut.spec`: configurazione PyInstaller esplicita per un
  bundle `.app` **directory-based** (mai `--onefile` su macOS GUI),
  `windowed`/`console=False`, `target_arch=None` (usa l'architettura
  dell'interprete che esegue la build, mai dichiarata universal2),
  `codesign_identity=None`/nessun `entitlements_file` (build non firmata,
  la firma è Milestone 5C). Include `src/pivotcut/resources/` come data
  files con lo stesso layout relativo sia in sorgente sia nel bundle.
  Nessun hidden import extra necessario: l'hook PySide6 di
  `pyinstaller-hooks-contrib` raccoglie già i plugin Qt richiesti
  (`QtWidgets`/`QtGui`/`QtCore` soltanto), e l'unico uso di Pillow in
  questo progetto (`Image.open`/`Image.save` su PNG) è coperto dal
  supporto PNG nativo di `PIL.Image`.
- Nuovo `src/pivotcut/runtime_paths.py`: unico punto del codice che ispeziona
  `sys.frozen`/`sys._MEIPASS`, con `package_root()`/`resource_path()` che
  risolvono allo stesso identico layout relativo sia da sorgente sia da
  bundle frozen. Non ancora usato da nessun modulo (nessuna risorsa reale
  in `resources/` finora), preparato per quando arriverà un'icona o un
  template.
- Nuovo `scripts/build_macos.sh`: verifica macOS, verifica interprete arm64
  nativo, verifica PyInstaller installato, pulizia sicura di `build/`/`dist/`
  (conferma interattiva o `--clean`), esegue l'intera suite `pytest` e
  interrompe la build se un test fallisce, invoca PyInstaller, verifica che
  `dist/PivotCut.app` esista davvero. Nuovo `scripts/clean_build.sh` per un
  cleanup indipendente.
- `pyproject.toml`/`requirements.txt`: aggiunto `pyinstaller` (+ le sue
  dipendenze dirette `altgraph`/`macholib`/`setuptools`/
  `pyinstaller-hooks-contrib`) come dipendenza di sviluppo.
- `.gitignore`: ignora artefatti di packaging (`*.app`, `*.dmg`,
  `*.spec.backup`) senza toccare `pivotcut.spec`, che resta versionato.
- Nessuna modifica al comportamento di Milestone 1-5A: `pytest` continua a
  riportare 217/217 test passati.
- Verifica manuale: build eseguita con `./scripts/build_macos.sh`,
  `dist/PivotCut.app` verificato esistente con eseguibile interno
  all'interno di `Contents/MacOS/`, avvio verificato sia con `open` sia
  eseguendo direttamente il binario interno (vedi report finale per i
  dettagli esatti e l'esito).

### Milestone 5A — Timeline con miniature, drag-and-drop e Undo/Redo

### Milestone 5A — Timeline con miniature, drag-and-drop e Undo/Redo
- Nuovo `domain/commands.py` (senza Qt): `Command` Protocol,
  `UndoRedoStack` (`execute`/`undo`/`redo`/`can_undo`/`can_redo`/`clear`,
  `DEFAULT_MAX_HISTORY = 100`), e tutti i comandi richiesti —
  `NewFrameCommand`/`DeleteFrameCommand` (avvolgono `domain.timeline` con
  cattura pigra al primo `apply()` per un redo fedele), `MoveFrameCommand`
  (Move Left/Right e drag-and-drop condividono la stessa funzione generica
  `domain.timeline.move_frame`), `CreateRigCommand`/`AddLayerCommand`,
  `TransformBoneCommand`/`EditLayerCommand`/`EditCameraCommand` (snapshot
  mirato del solo oggetto modificato, mai dell'intero `Project`).
- `domain/timeline.py`: `move_current_frame_left`/`right` ora delegano al
  nuovo `move_frame(project, from_index, to_index)` generalizzato — stessi
  identici risultati per lo spostamento di una posizione (verificato), più
  supporto per spostamenti arbitrari (drag-and-drop).
- Nuovo `services/export_renderer.render_thumbnail()`: riusa
  `render_frame_to_qimage()`, scala preservando l'aspect ratio con letterbox/
  pillarbox su tela trasparente. Nuovo `services/thumbnail_cache.py`:
  `ThumbnailCache` con chiave `(frame_id, revision, size, background_mode)`,
  `bump()`/`bump_all()` per invalidazione mirata o totale — nessun timer di
  debounce dedicato: il sistema di comandi (un comando per gesto di editing
  completato) è già il meccanismo di debounce.
- `ui/timeline_widget.py` riscritto: `FrameThumbnail` ora mostra
  un'immagine renderizzata reale (non più placeholder testuale) e gestisce
  drag-and-drop di reorder via `QDrag`/`QMimeData` con l'id stabile del
  frame come payload (mai l'indice), indicatore visivo del lato di
  inserimento, auto-scroll verso il frame corrente. New/Delete/Move/Reorder
  ora costruiscono `Command` ed eseguono tramite un `command_executor`
  iniettato da `MainWindow` invece di mutare `Project` direttamente.
- `ui/canvas_view.py`: il drag di un bone continua a mutare live per il
  feedback visivo, ma emette `bone_transform_committed` una sola volta al
  rilascio del mouse (se il valore è davvero cambiato), non per ogni pixel
  di movimento. `ui/inspector_panel.py`: nuova baseline "before" catturata
  ad ogni cambio di selezione/frame, commit (`layer_edit_committed`/
  `camera_edit_committed`) su `editingFinished`/toggle/Reset Camera, con
  confronto per struct-equality per evitare comandi vuoti se il valore
  torna quello di partenza.
- `app/main_window.py`: `UndoRedoStack` + `ThumbnailCache` condivisi,
  `_execute_command`/`_undo`/`_redo` centralizzano invalidazione cache,
  dirty flag e resync UI; menu Edit + toolbar con Undo/Redo (`Cmd+Z`/
  `Cmd+Shift+Z`, convenzioni Qt standard) con testo dinamico ("Undo Move
  Bone"); titolo finestra con `*` se `_is_dirty`, azzerato da Save/Save As,
  ripristinato pulito da New/Open; prompt Save/Discard/Cancel su
  New/Open/chiusura finestra con modifiche non salvate (`closeEvent`
  override); guardia `_can_mutate_model()` (`not is_exporting and not
  is_playing`) condivisa da playback ed export, con `_set_mutation_ui_enabled()`
  che disabilita timeline/inspector/asset panel/toolbar in entrambi i casi.
- 217 test automatici (`pytest`): 166 Milestone 1-4 invariati, 32 nuovi test
  su comandi/`UndoRedoStack` incluso il caso non-frame-selezionato durante
  un reorder (`test_history.py`), 8 nuovi test su rendering
  miniature/cache/invalidazione (`test_thumbnail_cache.py`), 11 nuovi test
  su `move_frame` generalizzato incluso il round-trip di salvataggio dopo
  un riordino (`test_timeline.py`/`test_project_io.py`).
- Verifica manuale completa (script Python con `QApplication` offscreen,
  vedi report finale): 3 pose con rig/layer/camera distinti → miniature
  verificate visivamente diverse (confronto byte-per-byte dei pixel
  renderizzati) → drag-and-drop reorder + Undo/Redo dell'ordine (incluso il
  caso in cui il frame trascinato non era quello selezionato) → modifica
  layer + bone drag, poi Undo/Redo di entrambi → duplica/cancella frame,
  poi Undo/Redo → Save azzera `*`, modifica successiva lo ripristina →
  New/Open/Close con modifiche non salvate mostrano il prompt
  Save/Discard/Cancel → export PNG con `is_exporting=True` verificato
  bloccare New Frame/Undo sia a livello di modello sia di widget disabilitati.

### Milestone 4 — Playback, export PNG e MP4
- Nuovo `domain/playback.py`: `expanded_timeline_indices`, `output_frame_count`,
  `output_duration_seconds`, `validate_playback_settings` — matematica pura,
  senza Qt, con l'esempio 4 pose/exposure 3/fps 24 → 12 frame/0.5s testato.
  `services/project_io.py` ora valida `fps > 0`/`exposure > 0`/
  `scene_settings.width > 0`/`height > 0` al caricamento.
- Nuovo `services/export_renderer.py`: rendering headless
  `render_frame_to_qimage`/`save_png_sequence`, con una pipeline dedicata
  (`_build_offscreen_scene`) che riusa la matematica e gli item Qt "senza
  stato" di `RigRenderer`/`LayerRenderer` senza mai dipendere da
  `CanvasView`/`MainWindow`/selezione, e senza mai includere overlay di
  selezione, cornice di output o griglia nell'immagine esportata. Asset
  mancanti producono lo stesso placeholder tratteggiato dell'editor, mai un
  crash.
- Nuovo `services/ffmpeg_export.py`: rilevamento FFmpeg (`PATH`, poi
  `/opt/homebrew/bin/ffmpeg`), invocazione via `subprocess.run` (nessuna
  shell) con il comando `ffmpeg -y -framerate {fps} -i frame_%06d.png -c:v
  libx264 -pix_fmt yuv420p -movflags +faststart -crf 18 {output}`,
  rendering in una `tempfile.TemporaryDirectory` sempre ripulita (anche in
  caso di errore/annullamento), verifica che il file finale esista e non
  sia vuoto prima di dichiarare successo.
- Nuovo `services/playback_controller.py`: `PlaybackController` basato su
  `QTimer`, stato esplicito `is_playing`, intervallo `1000/fps` ms, tiene
  ogni posa per `exposure` tick prima di avanzare, si ferma all'ultimo
  frame (MVP) oppure ricomincia dal primo se Loop è attivo. Non muta mai
  rig/layer/camera: scrive solo `current_frame_index`, tramite lo stesso
  `domain.timeline.select_frame` usato dalla navigazione manuale.
- `ui/canvas_view.py`: distingue Space+drag (pan, non attiva/disattiva il
  playback) da Space press/release senza drag (emette `playback_requested`);
  nuovo `set_playback_active()` blocca selezione/drag di bone e layer
  durante la riproduzione senza nascondere il canvas.
  `ui/timeline_widget.py`: nuovo `set_playback_active()` blocca
  navigazione/New/Delete/Move/fps/exposure durante la riproduzione (guardia
  anche a livello di singolo metodo, non solo sui pulsanti, così gli
  shortcut da tastiera restano coerenti).
- `app/main_window.py`: `PlaybackController` collegato a toolbar Play/Stop
  + Loop; un `eventFilter` a livello applicazione rende `Space` un toggle
  Play/Stop globale, escludendo `QLineEdit`/`QSpinBox`/`QDoubleSpinBox`/
  `QComboBox` (che continuano a ricevere lo spazio come testo/per aprire il
  menu) e il canvas (che gestisce da sé la distinzione pan/toggle). Nuove
  azioni "Export PNG Sequence…"/"Export MP4…" con `QFileDialog`,
  conferma prima di sovrascrivere file/cartelle esistenti,
  `QProgressDialog` con fase/percentuale/Cancel per entrambi i flussi di
  export (rendering sul thread principale con eventi processati da
  `QProgressDialog.setValue()`, mai un `QThread` — vedi limitazioni). Status
  bar estesa con "Pose X/N", "Video frame Y/totale", FPS, Exposure, durata
  stimata.
- 166 test automatici (`pytest`): 105 Milestone 1/2/3 invariati, 22 nuovi
  test su `domain/playback.py` (`test_playback.py`), 26 nuovi test sul
  rendering offscreen incluso il caso di asset mancante e l'esclusione
  degli overlay editor (`test_export_renderer.py`), 13 nuovi test su
  `ffmpeg_export.py` con FFmpeg completamente mockato via
  `unittest.mock.patch` su `subprocess.run`/`shutil.which`
  (`test_ffmpeg_export.py`) — nessuno richiede FFmpeg realmente installato.
- Verifica manuale completa (script Python con `QApplication` offscreen,
  vedi report finale): rig + 2 layer a `z_depth` diversi → 3 pose →
  fps=24/exposure=3 → playback che mantiene ogni posa esattamente 3 tick
  (`[0,0,0,1,1,1,2,2,2]`) e si ferma automaticamente all'ultima posa →
  Loop verificato separatamente (`[0,0,1,1,2,2,0,0,1,1]` con exposure=2) →
  export PNG sequence: 3 pose × exposure 3 = 9 PNG scritti e non vuoti →
  contenuto della scena canvas invariato (stesso numero di item) prima e
  dopo l'export → toggle Space globale verificato per 4 casi (widget
  generico, `QSpinBox` escluso, canvas senza drag, canvas con drag) →
  FFmpeg non disponibile su questa macchina di sviluppo, quindi l'export
  MP4 reale non è stato eseguito qui (il percorso "FFmpeg non trovato" con
  messaggio chiaro e suggerimento `brew install ffmpeg` è invece verificato,
  sia manualmente sia nei test automatici mockati).

### Milestone 3 — Layer, camera e 2.5D
- Nuovo `domain/layer.py` (`Layer`, `validate_layer`/`validate_layers`,
  `create_layer_from_asset`) e `domain/camera.py` (`Camera`, `parallax_factor`,
  `screen_position`, `zoom_matrix`, `layer_screen_matrix`,
  `character_plane_matrix`) — matematica pura, senza Qt, con esempi
  z_depth=0/1/3 documentati e testati.
- `Frame` esteso con `camera: Camera` e `layers: list[Layer]`; "New Frame"
  fa deep copy completa anche di camera e layer (id layer invariati tra
  frame, come rig/bone).
- `services/project_io.py`: serializzazione/deserializzazione di camera e
  layer con compatibilità totale con i file Milestone 1 e 2, validazione al
  caricamento (`validate_camera`, `validate_layers`).
- Nuova UI: `ui/inspector_panel.py` (campi layer/camera, Reset Camera),
  `ui/graphics_items.py` esteso con `LayerItem`/`MissingLayerItem`/
  `LayerRenderer`, `ui/canvas_view.py` esteso con selezione layer (mutua
  esclusione con la selezione bone), cornice output 1920x1080, e drag dei
  bone corretto per tenere conto di `camera.zoom`, `ui/asset_panel.py` con
  "Add Layer From Selected Asset" + selettore tipo.
- Il rig resta sul "piano personaggi" (z_depth implicito 0): riceve lo stesso
  offset camera dei layer con z_depth=0, calcolato in `domain.camera` e
  composto sopra la trasformazione FK pura (invariata da Milestone 2).
- 105 test automatici (`pytest`): 95 Milestone 1/2 invariati, 38 nuovi test
  su parallasse/camera/layer (`test_parallax.py`), più nuovi test su deep
  copy layer/camera nella timeline e round-trip/compatibilità/validazione
  della persistenza.
- Verifica manuale completa: import 2+ PNG → 2 layer con z_depth diversi →
  modifica camera x/y dall'Inspector → conferma numerica che il layer più
  lontano si sposta meno → modifica z-index → modifica layer dall'Inspector
  → Save/Open con rig+layer+camera → New Frame con isolamento del frame
  precedente; più verifica dei meccanismi UI (selezione mutuamente esclusiva
  bone/layer via evento mouse reale, correttezza del drag con camera.zoom
  attivo, placeholder per asset mancante senza crash).

### Milestone 2 — PNG e rig
- Nuovo `domain/assets.py` (dataclass `Asset` pura) e `domain/rig.py`
  (`Bone`, `Rig`, `Matrix2D`, `validate_rig`, `world_transforms`,
  `create_rig_from_asset`) — matematica delle trasformazioni 2D in Python
  puro, senza Qt, con cycle detection e validazione strutturale/semantica.
- `Frame` esteso con `rigs: list[Rig]` (deep copy su "New Frame", id
  invariati); `Project` esteso con `assets: list[Asset]`.
- `services/asset_manager.py`: validazione PNG con Pillow, deduplica per
  path risolto, derivazione/refresh di `relative_path`, picker `QFileDialog`.
- `services/project_io.py`: serializzazione/deserializzazione di asset e rig
  con compatibilità totale con i file Milestone 1 (`assets`/`rigs` assenti →
  liste vuote), validazione rig al caricamento.
- Nuova UI: `ui/asset_panel.py` (pannello Assets + "Create Rig From Selected
  Asset"), `ui/graphics_items.py` (`RigRenderer`, rendering "flat" da
  `world_transforms`, placeholder per asset mancanti), `ui/canvas_view.py`
  esteso con selezione/drag/rotazione di un bone.
- 57 test automatici (`pytest`): 25 Milestone 1 invariati, 25 nuovi test su
  composizione delle trasformazioni/validazione rig, 7 nuovi test su deep
  copy dei rig nella timeline e round-trip/compatibilità della persistenza.
- Verifica manuale completa: import PNG → create rig → save → reopen →
  render + selezione → new frame → drag/rotate → frame precedente invariato.

### Milestone 1 — Base funzionante
- Setup progetto (`pyproject.toml`, `requirements.txt`, struttura `src/`).
- Modello di dominio (`SceneSettings`, `Frame`, `Project`) come dataclass pure,
  indipendenti da Qt.
- Operazioni timeline pure e testate: nuovo frame duplicato (con suffisso
  label incrementale "(copy)"/"(copy 2)"), cancellazione con guardia
  sull'ultimo frame, riordino, navigazione con selezione che segue le regole
  richieste.
- Persistenza JSON versionata con eccezioni tipizzate (`InvalidProjectFileError`,
  `UnsupportedProjectFormatError`, `IncompleteProjectDataError`).
- MainWindow PySide6 con canvas, timeline, toolbar/menu New/Open/Save
  funzionanti, Play/Export disabilitati.
- 25 test automatici (`pytest`) su dominio e persistenza; verifica manuale
  della GUI (creazione/cancellazione/navigazione frame, round-trip
  salvataggio/apertura, zoom/pan canvas).
