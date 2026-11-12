## Novità 1.1.8

- **Manutenzione interna**: adeguamenti ai controlli di validazione di Home Assistant e HACS.

## Novità 1.1.7

### Push in tempo reale (webhook)
- **Stato applicato subito**: quando la centrale segnala una zona aperta o chiusa, un'uscita accesa o spenta, un'area disinserita o in allarme, l'entità cambia all'istante con lo stato scritto nella riga, senza aspettare la lettura della centrale. Subito dopo l'integrazione rilegge comunque la centrale.
- **Impulsi brevi delle uscite**: un'uscita che resta accesa meno di un giro di polling ora viene vista, con le due righe «accesa» e «spenta».
- **Nuova riga per le uscite**: `?ev=output&id=<uscita>&state=1` (accesa) e `state=0` (spenta). Senza parametri aggiuntivi, `?ev=output` fa partire la sola rilettura.
- **Numeri da scrivere nelle righe**: ora si leggono negli attributi delle entità: `zone_id` sulla zona, `output_id` sull'uscita, `partition_id` sull'area.

## Novità 1.1.6

### Zone doppie con firmware 5
- **Nome della seconda zona**: sulle centrali con firmware 5 la seconda zona di un morsetto a zona doppia compariva senza nome (una riga chiamata solo «Zone»). Ora ha il nome impostato sulla centrale.
- **Numero di zona corretto**: la seconda zona viene ora identificata con il numero che usa il firmware 5, anche per le impostazioni.
- **Esclusione della seconda zona**: l'interruttore «Esclusione» della seconda zona tornava spento da solo e la zona non veniva esclusa. Ora esclusione e riattivazione funzionano.

## Novità 1.1.4

### Connessione più stabile
- **Niente più entità «non disponibili» per pochi secondi**: se la centrale smette di rispondere per un attimo, le entità mantengono l'ultimo stato per 30 secondi. Passato quel tempo la caduta viene segnalata come prima.
- **Riconnessione più rapida**: dopo una caduta l'integrazione riprova ogni secondo invece che ogni 5. Se la centrale non torna entro 30 secondi, i tentativi rallentano a uno ogni 5 secondi.
- **I comandi non vanno persi**: un comando dato mentre la centrale non è raggiungibile resta in attesa (fino a 15 secondi) e parte appena torna la connessione.
- **Doppio tocco ignorato**: se ripeti lo stesso comando mentre il primo è ancora in attesa, il secondo viene scartato. Un cancello non riceve due impulsi.
- **Cadute periodiche gestite**: la centrale chiude le connessioni locali per circa 11 secondi a intervalli fissi di ~71,6 minuti (2³² microsecondi), quando riavvia le proprie connessioni di rete. L'integrazione le assorbe senza effetti visibili.

### Sensori di connessione
- **Nuovi attributi diagnostici** su «Connessione Native» e «Connessione PrimeLAN»: `ultima_caduta` e `durata_ultima_caduta`.
- **Meno scritture nello storico**: l'attributo `ultimo_ok` si aggiorna al massimo una volta al minuto.

## Novità 1.1.3

### Pannello allarme
- **Stati puliti:** niente più rimbalzi tra «In attivazione», «Disattivo» e «In disattivazione» durante i comandi. Un comando che sta ancora terminando non interferisce più con quello successivo.
- **Disinserimento più rapido:** nuova voce **«➜ Tutte le aree disinserite»** per il comando Disattivato. Passa dal protocollo Native invece che da PrimeLAN (su una centrale di prova circa 2 secondi contro 4,5–6). È la scelta consigliata.
- **Inserimento più rapido:** la conferma arriva dalle aree, senza aspettare la rilettura dello scenario (su una centrale di prova da 6–8 a circa 5 secondi).
- Durante un comando il polling si sospende, così la conferma non resta in coda.
- Se la centrale rifiuta un inserimento (es. zona aperta), il pannello smette di mostrare «In attivazione» dopo 15 secondi.

### Avvio
- **Avvio e ricarica più rapidi:** i nomi delle uscite PrimeLAN vengono salvati e riletti in sottofondo (su una centrale di prova da circa 19 a 10 secondi).
- **Prima installazione:** la connessione aperta dal modulo di configurazione viene riusata, senza connettersi due volte di seguito.
- La schermata **Tapparelle** delle opzioni si apre subito anche dopo un riavvio.

### Configurazione
- **Riconfigura** chiede solo modalità e connessione e conserva tutte le altre opzioni (prima si potevano perdere PIN, webhook e tapparelle).
- Dopo una nuova installazione il **Registro eventi** viene creato correttamente.
- Log di debug con i tempi di connessione, avvio e comandi allarme.

## Novità 1.1.2

### Più reattiva
- **Disinserimento più rapido:** il pannello torna a «disinserito» subito, confermando dallo stato delle aree invece di aspettare qualche secondo.
- **Reload senza errore rosso:** alla ricarica dell'integrazione la connessione ritenta per ~30s prima di segnalare un problema (la centrale libera la connessione nativa con un po' di ritardo), così non compare più l'errore temporaneo.

## Novità 1.1.1

### Allarme
- Nuovo **pannello allarme con PIN** opzionale: una seconda entità che chiede il PIN **solo per disinserire** (l'inserimento resta libero), comoda per un tablet a parete. Quella originale, senza PIN, resta per il telefono.
- Testi della schermata allarme più chiari e nota sulle **zone autoescludibili** (con PrimeLAN lo scenario non parte se una zona autoescludibile è aperta → usa le aree).

### Push in tempo reale (nuovo, opzionale)
- La centrale può inviare gli eventi a Home Assistant via **webhook locale** (PrimeLAN → «Gestione Api Http in uscita»): le entità si aggiornano subito. Interruttore **«Accetta richieste non locali»** per i setup dietro reverse proxy.

## Novità 1.1.0

### Pannello allarme
- Pannello ad aree con la modalità Native: per ogni comando scegli la modalità di ciascuna area (Totale / Parziale / Istantaneo).
- Pannello ibrido con Native + PrimeLAN: ogni comando può usare uno scenario della centrale oppure aree personalizzate (es. Vacanza ad aree, gli altri a scenario).
- Attivazione uscite: un comando ad aree può anche attivare una o più uscite (es. chiusura persiane).
- Gli scenari si scelgono per nome, non più per numero.

### Opzioni ridisegnate
- Menù con sezioni separate: Parametri generali, Intervalli di scansione, Pannello allarme, Tapparelle (solo se la centrale ha moduli tapparella).
- Pannello allarme in una sola schermata: un gruppo per comando, con gli stessi nomi e ordine della card di Home Assistant.
- Un unico «Salva ed esci»: nulla viene scritto finché non salvi.
- Il primo setup chiede solo la connessione; il resto si regola dalle Opzioni.
- «Backend» ora si chiama «Modalità di integrazione» (si cambia da Riconfigura).

### Prestazioni
- Avvio molto più rapido: connessioni in parallelo e letture non essenziali spostate in background (su una centrale di prova da ~28 s a ~13 s).
- La schermata Tapparelle si apre subito (nomi dei moduli in memoria).
