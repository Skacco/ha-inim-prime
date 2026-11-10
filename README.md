> **Fork gestito da [skaccox](https://github.com/skaccox).** Rispetto all'originale aggiunge: il **backend Native** (protocollo locale TCP, veloce), le **uscite** (switch/luci), gli **scenari** (pulsanti + sensore scenario attivo), un **pannello di allarme** guidato dagli scenari, i **timer**, i **termostati** (sensori di temperatura), le **tapparelle** (comandate dalle uscite virtuali), il raggruppamento dei dispositivi e l'**interfaccia in italiano**.
> Basato su [Pitscheider/ha-inim-prime](https://github.com/Pitscheider/ha-inim-prime) (GPL-3.0).

# Introduzione
Questa integrazione personalizzata permette a Home Assistant di comunicare con le **centrali di allarme Inim Prime**.

## Modalità di integrazione
Puoi usare due backend, **singolarmente o insieme**. La scelta si fa durante la configurazione dell'integrazione.

### Native — protocollo locale (TCP)
Comunica **direttamente con la centrale** sulla porta del protocollo locale (TCP, default **6004**), con cifratura AES. È il canale **veloce e completamente locale**: non passa dal cloud né dalla Web API.
Espone:
- **zone** (stato aperto/chiuso, memoria allarme, esclusione)
- **partizioni** (stato, inserimento/disinserimento, memoria allarme)
- **uscite** (switch/luci)
- **timer**
- **termostati** (sensori di temperatura)
- **tapparelle** (comandate tramite le uscite-comando della centrale)

Richiede solo la raggiungibilità della porta del protocollo locale e la relativa password (default `pass`). **Non** richiede la scheda PrimeLAN.

### PrimeLAN — Web API ufficiale Inim
Usa la **Web API ufficiale Inim** esposta dalla scheda di rete **PrimeLAN**. Aggiunge le funzioni che il protocollo locale non espone:
- **scenari** (un pulsante per scenario + sensore "Scenario attivo")
- **stato GSM** (operatore, potenza segnale, credito)
- **guasti di sistema**
- **registro eventi** della centrale

Richiede la **scheda di rete PrimeLAN** installata nella centrale.

### Entrambi (consigliato)
Attivi i due backend insieme: **Native** fa il lavoro locale e veloce (zone, partizioni, uscite, tapparelle, termostati, timer), **PrimeLAN** aggiunge scenari, GSM, guasti e registro. Il **pannello di allarme** di Home Assistant unisce i due mondi: legge lo stato dalle partizioni e invia i comandi di inserimento/disinserimento tramite gli **scenari** PrimeLAN.

## Compatibilità firmware
Testata sia con centrali a **firmware 4.x** sia a **firmware 5.x**.

Il progetto è **sviluppato dalla community**, **non affiliato a INIM Electronics**, ed è pensato per **affiancare — non sostituire** il software ufficiale Inim.

# Avvertenze
Questa integrazione è fornita **così com'è** ed è in **sviluppo attivo**. Va considerata **sperimentale**.
Non tutte le funzioni della centrale Inim Prime sono supportate, alcuni comportamenti possono essere incompleti o errati e **possono verificarsi modifiche che rompono la compatibilità in qualsiasi momento**, senza preavviso.
Questa integrazione interagisce con un **sistema di sicurezza/allarme**.
Usala **a tuo rischio** e **non affidarti ad essa come unico sistema di controllo o monitoraggio** della tua centrale.
L'autore **non si assume alcuna responsabilità** per:
- Malfunzionamenti o comportamenti imprevisti
- Eventi mancati o in ritardo
- Stati della centrale errati
- Qualsiasi danno, perdita o problema di sicurezza derivante dall'uso dell'integrazione

Verifica sempre le operazioni critiche direttamente sulla centrale Inim Prime o tramite il software ufficiale Inim.

# Note di sicurezza
- L'implementazione HTTPS di PrimeLAN usa un TLS datato.
- Si consiglia caldamente di usare questa integrazione **solo su una LAN fidata**.
- Non esporre mai la centrale direttamente su Internet.

# Configurazione della centrale Inim Prime
## Requisiti
- Per il backend **PrimeLAN** serve una centrale Inim Prime con la **scheda di rete PrimeLAN**: senza quella scheda PrimeLAN non è utilizzabile (puoi acquistarla e installarla da solo o tramite un installatore). Il manuale della scheda è qui: [manuale Inim PrimeLAN](https://www.manualslib.com/manual/3494645/Inim-Primelan.html?page=4#manual).
- Per il backend **Native** serve la raggiungibilità della porta del protocollo locale della centrale (TCP, default **6004**) e la password del protocollo (default `pass`).

In entrambi i casi si configura la centrale con il software **Prime/STUDIO** su Windows (download: [Inim Prime/STUDIO](https://archive.org/details/primestudio_4002_setup)).

## Prepara la centrale
### Connessione alla centrale
Apri Prime/STUDIO:

<img width="2560" height="1382" alt="img00" src="https://github.com/user-attachments/assets/9a2017b5-a9d2-4dc2-91ef-f195e755ffc7" />

Clicca su `Open panel connected`, poi seleziona `Prime LAN`:

<img width="905" alt="img01" src="https://github.com/user-attachments/assets/9507ebab-88d9-46d6-84a3-f51cedd98854" />

Inserisci IP e password della centrale (default `pass`) e clicca `Next`.
Verificata la comunicazione, vedrai la centrale rilevata, la versione firmware e il **numero di serie**.
È **FORTEMENTE CONSIGLIATO** copiare correttamente il numero di serie e conservarlo: servirà dopo nella configurazione di Home Assistant.
Poi clicca `Open the panel`.

### Fai un backup della configurazione
La prima cosa da fare, se non l'hai già fatto, è copiare la configurazione della centrale.
Scarica la configurazione attuale cliccando sull'icona `Download` in alto a sinistra:

<img width="634" height="113" alt="img02" src="https://github.com/user-attachments/assets/14a5bdee-61f4-4811-a8a0-f3a0932b18a2" />

Al termine clicca `Save`, scegli un nome e `OK`.
In caso di problemi potrai ripristinare aprendo (`Open`) la configurazione salvata e premendo l'icona `Upload`.

### Controlla le impostazioni di rete
Dopo il backup, clicca `Control panel parameters` nel pannello di sinistra:

<img width="265" height="603" alt="img03" src="https://github.com/user-attachments/assets/6f3d5703-8dd9-416a-8562-cd8c72a93674" />

Poi `IP connection parameters`:

<img width="693" height="83" alt="img04" src="https://github.com/user-attachments/assets/283bb9bc-3dec-4172-b275-bf16d8bb2944" />

Controlla se è attivo `Obtain an IP address automatically`.
Se è attivo, la centrale usa parametri ottenuti dal DHCP: in Home Assistant serve un IP statico (o almeno un hostname). Se vuoi tenere il DHCP, riserva un IP statico per la centrale sul tuo server DHCP (di solito il router). Se preferisci disabilitare il DHCP, imposta i parametri e annota l'indirizzo IP, che userai nella configurazione dell'integrazione.
Dopo le modifiche, carica la configurazione aggiornata con `Upload`:

<img width="557" height="99" alt="img05" src="https://github.com/user-attachments/assets/cbf3c2b3-b2b0-44d0-8ddf-d0ca6fd3612c" />

### Aggiorna il firmware
Consigliato aggiornare il firmware della centrale, cliccando qui (in alto a destra):

<img width="112" height="102" alt="img06" src="https://github.com/user-attachments/assets/79168438-cfd7-42e8-93f4-d3dfaf3d19d9" />

### Configura PrimeLAN (solo per il backend PrimeLAN)
A centrale aggiornata, clicca `PrimeLAN settings` nel pannello di sinistra:

<img width="298" height="603" alt="img07" src="https://github.com/user-attachments/assets/1d949b2c-cdca-49a8-b6c6-d9b8feb2ed98" />

Prima di operare, carica la configurazione attuale con l'icona di download in alto a sinistra (può volerci un po'):

<img width="940" height="1019" alt="img08" src="https://github.com/user-attachments/assets/2bf70476-4c85-4044-9e19-081b6c6b6223" />

Poi clicca `Web API` in alto a destra:

<img width="915" height="964" alt="img09" src="https://github.com/user-attachments/assets/c6742134-35d5-4fe2-b3bb-e82b51f3140d" />

Attiva `API HTTP/HTTPS enable`, poi `Generates` per creare una nuova API Key.
La API Key ti servirà nella configurazione di Home Assistant. Dà accesso a gran parte delle funzioni della centrale: se pensi sia compromessa, rigenerala (ricordandoti di ricaricare la configurazione dopo).
Seleziona quale `Code` userà la Web API per le operazioni. Consigliato creare un codice dedicato solo a questo scopo.
Consigliato attivare `Only Allow connections via HTTPS` per cifrare le comunicazioni (il protocollo è datato, ma su LAN va bene).
L'opzione `Not allow disalarm` è a tua scelta: se attiva, alcune operazioni falliranno e al momento l'integrazione non supporta bene questo caso.
Al termine clicca `Upload` per caricare la nuova configurazione.

# Installazione in Home Assistant
## Tramite HACS (consigliato)
È il modo consigliato perché gestisce gli aggiornamenti.
### Requisiti
Devi avere HACS installato in Home Assistant. Se non ce l'hai: [https://www.hacs.xyz/docs/use/](https://www.hacs.xyz/docs/use/).
### Aggiungi il repository personalizzato
Apri HACS → menu in alto a destra (tre puntini) → `Custom repositories`.
Nel campo `Repository` incolla `https://github.com/skaccox/ha-inim-prime`, nel menu `Type` scegli `Integration`, premi `ADD`.
### Installa l'integrazione
In HACS cerca `Inim Prime`, aprila, premi `DOWNLOAD` due volte.
### Riavvia Home Assistant
Dopo il download comparirà un avviso `Restart required`: riavvia Home Assistant per completare l'installazione.
## Installazione manuale (sconsigliata)
Scarica il codice sorgente dell'ultima release da [https://github.com/skaccox/ha-inim-prime](https://github.com/skaccox/ha-inim-prime), copia la cartella `inim_prime` dentro `custom_components` della tua configurazione Home Assistant e riavvia.

# Configurazione dell'integrazione in Home Assistant
Vai in `Impostazioni` → `Dispositivi e servizi` → `Aggiungi integrazione`, cerca `Inim Prime`.

### Scelta del backend
All'inizio scegli quale backend usare: **Solo Native**, **Solo PrimeLAN** o **Native + PrimeLAN**.

### Configurazione iniziale
- **Numero di serie** — incollalo (quello copiato da Prime/STUDIO). Non viene verificato dall'integrazione e **non è modificabile in seguito**: identifica l'installazione e determina dove vengono salvati i dati. Se non ce l'hai, un'alternativa è il MAC di rete.
- **Host** — IP o hostname della centrale (riserva un IP statico o un record DNS se usi il DHCP).
- Sezione **Native** (se attivo): porta (default 6004), password del protocollo (default `pass`), eventuale PIN personalizzato.
- Sezione **PrimeLAN** (se attivo): `Usa HTTPS` (consigliato) e la `Chiave API` generata in Prime/STUDIO.

Poi puoi configurare le opzioni (vedi sotto); se hai dubbi lascia i valori predefiniti.
La prima configurazione può richiedere un po': è normale, la centrale Inim Prime non è velocissima 😅.

### Fine setup
Al termine vedrai i dispositivi creati dall'integrazione, che puoi rinominare e assegnare a un'Area. Premi `Fine`.

# Riconfigurazione
Per cambiare host, HTTPS, chiave API, porta/password/PIN o passare da un backend all'altro: apri l'integrazione, clicca i tre puntini del dispositivo (nome di default `INIM Prime (numero_di_serie)`) e premi `Riconfigura`.

# Opzioni
Clicca l'icona dell'ingranaggio accanto alla centrale. Puoi configurare:

## Limite eventi del registro da scaricare
Numero massimo di eventi scaricati quando vengono rilevati nuovi log. Consigliato il valore predefinito; l'integrazione ne scarica automaticamente di più se serve. Non è in tempo reale (il recupero è asincrono).

## Intervalli di scansione
Ogni quanto Home Assistant interroga la centrale (valori più bassi = più traffico). Ogni campo indica il **backend** usato: **Zone / Partizioni / Uscite** sono lette dal **Native** se configurato (locale, veloce), altrimenti da **PrimeLAN**; **GSM / Guasti / Eventi registro / Scenari** sono **solo PrimeLAN**.

⚠️ Il backend **Native** supporta intervalli molto più bassi di PrimeLAN per zone, partizioni e uscite (polling locale).

ℹ️ Quando usi **entrambi** i backend, i dati "vivi" arrivano dal Native, quindi i poller **solo-PrimeLAN** partono con un **default più lento (60s per GSM, guasti ed eventi registro)** per ridurre il traffico PrimeLAN; gli **scenari** restano più reattivi perché guidano il pannello allarme. Puoi comunque cambiare ogni valore nelle opzioni.

## Pannello allarme
L'integrazione crea un'entità `alarm_control_panel` per inserire/disinserire l'impianto. Un comando può essere eseguito in **due modi**, che si differenziano per una cosa importante: cosa succede con **zone/finestre aperte**.

- **Scenario** (via PrimeLAN): attiva uno **scenario** della centrale. Lo scenario applica i **controlli della centrale**, quindi con una zona aperta non auto-escludibile **si rifiuta di armare e avvisa**. È l'equivalente del comportamento di sdomotica.
- **Partizioni** (via Native): agisce **direttamente sulle partizioni**, **forzando l'inserimento** (bypassa il controllo dello scenario). Le zone auto-escludibili vengono escluse; quelle non auto-escludibili vengono comunque segnalate/bloccate dalla centrale. È **quasi istantaneo** (tutto locale). Permette di **mescolare le modalità** (alcune partizioni in Totale, altre in Istantaneo nello stesso comando), cosa che molti scenari non fanno.

### Come si configura
Le opzioni si aprono con un **menù**: *Parametri generali*, *Intervalli di scansione*, *Pannello allarme* e — **solo se la centrale ha moduli tapparella** — *Tapparelle*. In *Pannello allarme* c'è un elemento per ciascun comando (Disinserimento / Fuori casa / In casa / Notte / Vacanza) e una voce **‹ Indietro** per tornare al menù principale.

Scegli un comando e, **con entrambi i backend**, scegli il **Metodo** da un menù (*Scenario* / *Partizioni* / *‹ Indietro*):
- **Scenario** → la schermata dopo chiede solo il **numero di scenario**.
- **Partizioni** → la schermata dopo mostra solo **una riga per partizione** (*Disinserita* / Totale / Parziale / Istantaneo) più **Attivazione uscite**.

Vedi **solo** i campi del metodo scelto; una partizione ha **una sola modalità** per comando (stato doppio impossibile). Ogni schermata termina con il **Salva** standard (salva e chiude); per tornare indietro usa la freccia di HA in alto. Per modificare un altro comando riapri le Opzioni.

Esempio tipico (entrambi i backend):
- **Fuori casa** → Metodo *Scenario*, Scenario = `2` (mantieni l'avviso finestre aperte per l'uso quotidiano)
- **Notte** → Metodo *Scenario*, Scenario = `3`
- **Vacanza** → Metodo *Partizioni*, poi su ogni partizione la modalità (es. perimetrali *Totale*, volumetrici interni *Istantaneo*, le altre *Disinserita*) — forza l'inserimento con un mix che gli scenari non replicano
- **Disinserimento** → Metodo *Scenario*, Scenario = `1` (oppure *Partizioni* senza nulla = spegne tutte)

> Con un solo backend il Metodo non viene chiesto: solo Native → sempre a partizioni; solo PrimeLAN → sempre a scenari.

Le partizioni non elencate restano disinserite. Lo **stato** mostrato in HA: una partizione in allarme → *triggered*; altrimenti, se un comando a partizioni combacia esattamente con lo stato attuale lo mostra, altrimenti vale lo scenario attivo, altrimenti *disinserito*/*custom bypass*.

**Attivazione uscite.** Un comando a partizioni può anche attivare **una o più uscite** (es. "Chiusura PI" per chiudere le persiane); la centrale rilascia da sola le uscite monostabili. Per i comandi a scenario ci pensa lo scenario stesso, quindi il campo uscite vale solo quando il comando è a partizioni.

### Quale pannello viene creato
- **Solo Native** → blocchi **a partizioni** (niente campo Scenario).
- **Solo PrimeLAN** → mappatura **a scenari** (un numero di scenario per comando; senza scenario di disinserimento il pannello non viene creato).
- **Entrambi i backend** → **un unico pannello ibrido**: per ogni comando scegli scenario o partizioni con la regola sopra.

## Tapparelle
Il modulo tapparella Smart esegue l'intera corsa da un singolo impulso e **non ha stop**: non si pilotano direttamente i relè motore (il modulo si riprende il relè e la discesa non funziona). Si comanda invece tramite le **uscite** della centrale.

**Prerequisito sulla centrale (INIM/SmartLiving).** Prima di configurare l'integrazione devi creare, sulla centrale, delle **uscite su terminali virtuali** — una per ogni posizione che vuoi comandare — e assegnare a ciascuna il relativo **comando di regolazione della tapparella**. Servono **5 uscite per ogni tapparella**, una per comando:

- **Aperto** (apertura totale)
- **Chiuso** (chiusura totale)
- **1/4** (25%)
- **1/2** (50%)
- **3/4** (75%)

Conviene dare a queste uscite nomi riconoscibili (es. "Apri tenda sala", "Chiudi tenda sala", "Tenda sala 1/2", …).

**Configurazione nell'integrazione.** Opzioni → **Tapparelle** (la voce compare solo se la centrale ha moduli tapparella). Il nome di ogni tapparella è preso in automatico dal suo modulo Mod.dom (è il titolo della sezione, non modificabile qui). Per ciascuna scegli l'uscita-comando per ogni posizione: **Aperto** e **Chiuso** sono obbligatorie, **1/4 · 1/2 · 3/4** sono facoltative e, se impostate, abilitano lo slider di posizione. La tendina elenca solo le uscite-comando: i terminali motore grezzi (`Mod.dom XX T03/T04`), usati solo per rilevare le tapparelle, non sono selezionabili. Una tapparella senza i comandi Aperto/Chiuso non viene creata.

## Mostra uscite non disponibili via PrimeLAN
Se spento mostra solo le uscite che PrimeLAN pubblica all'utente collegato; se acceso mostra anche le uscite interne/scheda madre (sirena, aux…).

## Nascondi aree inutilizzate
Nasconde le aree mai nominate nella centrale (col nome predefinito tipo `AREA 013`) e le loro entità.

# Dispositivi disponibili
L'integrazione raggruppa le entità in pochi dispositivi.

## Centrale Inim Prime
Rappresenta la centrale.
- **Sensori:** Zone escluse, Memoria allarme zone, Memoria allarme partizioni, Scenario attivo.
- **Diagnostica:** Tensione alimentazione, i sensori di **guasto** (Batteria scarica, Guasto rete, Linea telefonica assente, Disturbo radio, Sabotaggio, Guasto GSM, Guasto Internet, ecc.) e i sensori di **connessione** per backend (**Connessione Native** e/o **Connessione PrimeLAN**, `connectivity`): accesi quando l'ultimo polling del backend è andato a buon fine, con l'attributo `ultimo_ok`. Accanto, i sensori **Connessione Native/PrimeLAN ultimo aggiornamento** (`timestamp`) espongono lo stesso orario come entità a sé (comodi in card/grafici) e restano "congelati" sull'ultimo momento buono quando il link cade. Anche di questi ne compare uno per ogni backend configurato — due se usi entrambi.
- **Registro eventi** (event entity): traccia i log della centrale. Non è in tempo reale (vedi sezione sulla card del registro).
- **Pulsanti:** Annulla esclusioni zone, Azzera memorie partizioni, e i pulsanti degli **scenari**.
- **Allarme** (`alarm_control_panel`): inserimento/disinserimento guidato dagli scenari (se configurato nelle opzioni).

## Zone
Device unico che contiene tutte le zone. Per ogni zona:
- **Binary sensor** (nome della zona) — stato aperto/chiuso. Consigliato impostarne il tipo con `Mostra come` (Porta, Finestra, Movimento…).
- **Stato** — uno tra FAULT / READY / ALARM / SHORT_CIRCUIT.
- **Esclusione** (switch) — mostra e cambia lo stato di esclusione/bypass.
- **Memoria allarme** (diagnostica).

## Partizioni
Device unico con tutte le partizioni. Per ognuna:
- **Stato inserimento** (select) — DISARMED / INSTANT / PARTIAL / TOTAL.
- **Stato** — ALARM / READY / SABOTAGE.
- **Azzera memoria** (pulsante).
- **Memoria allarme** (diagnostica).

## Uscite
Device unico con tutte le uscite della centrale, come interruttori (`switch`) o luci dimmerabili (`light`).

## Tapparelle
Device unico con le tapparelle configurate (cover con Apri/Chiudi ed eventuale posizione).

## Termostati
Device unico con i sensori di temperatura dei termostati (solo backend Native, se presenti sulla centrale).

## Timer
Device unico con i timer della centrale: un binary sensor "attivo" e uno switch "abilitato" per ciascuno. Creato **solo se la centrale ha dei timer** (come Termostati, Tapparelle e Uscite).

## GSM
Operatore, Potenza segnale, Credito, Tensione alimentazione.

# Configurazione consigliata
## Rinomina i dispositivi
Rinomina zone e partizioni con nomi parlanti (funzionano per id, quindi rinominarle è sicuro).
## Disabilita i dispositivi inutilizzati
Puoi disabilitare le entità di zone/partizioni che non usi.
## Scegli il tipo giusto per i binary sensor delle zone
Per ogni zona apri il binary sensor, icona ingranaggio, e sotto `Mostra come` scegli Porta, Finestra, Movimento, ecc.

## Card del registro eventi (senza card personalizzate)
La card **Markdown** integrata può mostrare il registro come tabella leggendo l'attributo `events` dell'entità evento. Crea una card Markdown e incolla:
```yaml
type: markdown
title: Registro centrale
entity_id:
  - event.inim_prime_panel_log_events
content: >
  | Ora | Evento | Chi | Come |

  |---|---|---|---|

  {% for e in state_attr('event.inim_prime_panel_log_events', 'events') or [] -%}

  | {{ as_timestamp(e.timestamp) | timestamp_custom('%d/%m %H:%M:%S') }} | {{ e.type }} | {{ e.location or '' }} | {{ e.agent or '' }} |

  {% endfor %}
```
Sostituisci `event.inim_prime_panel_log_events` con l'id dell'entità evento della tua centrale. L'attributo `events` contiene gli ultimi eventi, ciascuno con `timestamp`, `type`, `location` e `agent`; adatta colonne e formato data a piacere. Per la larghezza intera usa una vista di tipo **Pannello**.

### Registro in un box con scroll (card-mod)
Il registro può diventare lungo e "spaginare" la vista. Per racchiuderlo in un **box ad altezza fissa con scroll interno** (titolo fermo, tabella che scorre) usa [card-mod](https://github.com/thomasloven/lovelace-card-mod) (installabile da HACS). Stessa card Markdown di sopra, con `grid_options` e `card_mod` aggiunti:
```yaml
type: markdown
title: Registro centrale
entity_id:
  - event.inim_prime_panel_log_events
content: >
  | Ora | Evento | Chi | Come |

  |---|---|---|---|

  {% for e in state_attr('event.inim_prime_panel_log_events', 'events') or [] -%}

  | {{ as_timestamp(e.timestamp) | timestamp_custom('%d/%m %H:%M:%S') }} | {{ e.type }} | {{ e.location or '' }} | {{ e.agent or '' }} |

  {% endfor %}
grid_options:
  columns: full
  rows: 8
card_mod:
  style: |
    ha-card {
      height: 100%;
      display: flex;
      flex-direction: column;
    }
    ha-card .card-content {
      overflow-y: auto;
    }
```
`rows` fissa l'altezza del box (in una vista a sezioni, 1 riga ≈ 56px: 8 righe ≈ una card media); l'`overflow-y: auto` sul contenuto fa scorrere la tabella senza spostare il resto. Aumenta o diminuisci `rows` per cambiare l'altezza.

# Informazioni sulla libreria API
L'integrazione comunica con la centrale tramite una libreria Python creata da Pitscheider, disponibile qui:
https://github.com/Pitscheider/inim_prime_api
Puoi usarla per testare l'API della tua centrale a basso livello o per creare qualcosa di nuovo.

# Marchi
INIM e Inim Prime sono marchi o marchi registrati di INIM Electronics S.r.l.
Questo progetto **non è affiliato, approvato o supportato da INIM Electronics S.r.l.**

# Licenza
Questo progetto è rilasciato sotto **GNU General Public License v3.0 (GPL-3.0)**.
Sei libero di usarlo, modificarlo e ridistribuirlo nei termini della licenza GPL-3.0. Vedi il file `LICENSE` per i dettagli.
