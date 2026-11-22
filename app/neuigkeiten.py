"""
„Was ist neu“ – das Funktions-Log der Vereinsverwaltung.

EINE gepflegte Quelle für alle Vereine: Jeder Eintrag beschreibt in Klartext,
was die App neu kann. Beim Deploy fährt die Liste automatisch überall mit.

Regeln für Einträge (bitte einhalten, die Tests prüfen das):
- datum   ISO (JJJJ-MM-TT) – der Tag, an dem die Funktion live ging
- titel   kurz und konkret, ohne Technik-Jargon
- text    1–3 Sätze Klartext aus Sicht der Nutzer:innen, gegendert
- fuer    "alle" (Mitglieder + Vorstand + Freund:innen) oder "vorstand"
- module  optional: nur zeigen, wenn der Verein ALLE genannten Module nutzt;
          erlaubt sind die modul_*-Namen der Einstellungen (ohne Präfix),
          z. B. veranstaltungen, schichten, schluessel, kinder, dms, werkhaus

Neue Funktion deployt? → Eintrag OBEN ergänzen (neueste zuerst).
"""

MODULE_BEKANNT = {"adventskalender", "aktuelles", "contenthub", "dms", "geburtstage",
                  "jubilaeen", "kinder", "rechnungen", "schichten",
                  "schluessel", "signage", "spenden", "veranstaltungen", "verteilen", "werkhaus"}

EINTRAEGE = [
    {"datum": "2026-11-22", "fuer": "vorstand",
     "titel": "Eigene Spendenaktion, Vermietung und Kino-Name einstellbar",
     "text": "Unter Signage → „Hausfolien anpassen“ tragt ihr selbst ein, ob ihr Räume vermietet, welche "
             "Spendenaktion (Link, Überschrift, Text) und welche Werte auf „Wer wir sind“ erscheinen und seit wann "
             "es euren Adventskalender gibt. Die Spendenaktion zeigt sich auch in der App und auf der "
             "Adventskalender-Seite. In den Einstellungen gibt es dazu den Namen eures Kinos und das kleine "
             "Feier-Symbol beim Abhaken von Aufgaben."},
    {"datum": "2026-11-21", "fuer": "vorstand", "module": ["signage"],
     "titel": "Signage: Folienplan, Gerätesymbole, Schaufenster und Aufsteller",
     "text": "Alle Folien stehen jetzt in einem Folienplan mit Vorschaubild (Klick = groß, auch im Hochformat): "
             "per Ziehen sortieren, mit dem Auge ein- und ausblenden "
             "und für jede Folie die Sekunden festlegen – oder alle auf einmal. Jedes Gerät zeigt sein Symbol "
             "(z. B. iPad oder Fernseher). Die Rollen heißen jetzt Schaufenster und Aufsteller (früher Türschild), "
             "und neu ist die Rolle Check-in: Gäste checken am Eingang selbst mit dem QR-Code ihres Tickets ein – "
             "bei Warteliste, offenem Betrag oder doppeltem Ticket schickt das Gerät sie freundlich zum Einlass. Zum 1. Mai zeigt die Festfolie eine rote Nelke, und "
             "„Folge uns!“ kommt als Pinnwand mit großen Logos daher. Neu im Folienplan: Schulferien in MV – "
             "„Schöne Herbstferien!“ mit Terminen aus der Ferienzeit und ein Countdown fünf Tage vorher."},
    {"datum": "2026-11-21", "fuer": "vorstand", "module": ["signage"],
     "titel": "Signage: Feiertage, Wer wir sind, Willkommen in sechs Sprachen",
     "text": "An allen gesetzlichen Feiertagen in Mecklenburg-Vorpommern zeigen die Bildschirme eine festlich gestaltete "
             "Folie mit Bezug zu Waren. Neu sind außerdem eine Folie über den Verein und eine Willkommensfolie, die "
             "zwischen Deutsch, Ukrainisch, Spanisch, Englisch, Italienisch und Vietnamesisch wechselt."},
    {"datum": "2026-11-21", "fuer": "vorstand", "module": ["signage"],
     "titel": "Signage erzählt: Begrüßung, So war's, Countdown",
     "text": "Die Bildschirme begrüßen nach Tageszeit mit dem Wetter am Vereinsort, zeigen unter „So war's“ "
             "freigegebene Fotos der letzten Veranstaltungen mit Einladung zum nächsten Mal und zählen bei "
             "Highlights die Tage herunter. Im Adventskalender bleibt das Türchen von morgen ein Geheimnis – "
             "nur Ort und Uhrzeit sind zu sehen."},
    {"datum": "2026-11-21", "fuer": "vorstand", "module": ["signage"],
     "titel": "Signage: Adventskalender, Hausfolien, Teaser",
     "text": "Die Bildschirme zeigen in der Adventszeit eine eigene Folienreihe zum Lebendigen Adventskalender mit "
             "aufschwingenden Türchen, Sternen und Glitzer. Dazwischen laufen Hausfolien: Mitmachen im Ehrenamt, "
             "Unterstützen, Raumvermietung und eure Kanäle zum Folgen – auf Wunsch zufällig gemischt. Beim Termin gibt es "
             "jetzt ein Feld „Kurztext / Teaser“, das auf den Bildschirmen erscheint."},
    {"datum": "2026-11-21", "fuer": "vorstand", "module": ["signage"],
     "titel": "Signage: eigene Folien und Status",
     "text": "Unter Öffentlichkeit → Signage schreibt ihr eigene Text-Folien oder ladet Bilder hoch, z. B. Plakate "
             "aus Canva; sie laufen zwischen den Termin-Folien, auf Wunsch nur in einem Zeitraum oder auf bestimmten "
             "Bildschirmen. Termin-Folien zeigen jetzt auch, ob Plätze frei sind, eine Anmeldung nötig ist, der Termin "
             "ausgebucht oder abgesagt ist, und nennen den Sponsor."},
    {"datum": "2026-11-21", "fuer": "vorstand", "module": ["signage"],
     "titel": "Signage: Bildschirme und Türschilder",
     "text": "Unter Öffentlichkeit → Signage koppelt ihr Monitore und alte iPads mit einem Code, ohne am Gerät "
             "zu tippen. Ein Schaufenster zeigt automatisch die Termine von heute und dieser Woche, Highlights mit "
             "Bild und QR-Code zur Anmeldung; ein Türschild zeigt, was im Raum gerade läuft und was danach kommt. "
             "Gezeigt wird nur Öffentliches, interne Termine erscheinen am Türschild als „Mitgliederveranstaltung“."},
    {"datum": "2026-11-21", "fuer": "alle",
     "titel": "Face ID noch sicherer",
     "text": "Ein neues Gerät für Face ID oder Fingerabdruck lässt sich nur kurz nach einer Anmeldung einrichten, "
             "und du bekommst dazu eine Bestätigungs-Mail. Der Vorstand kann eingerichtete Geräte unter „Zugänge“ "
             "sehen und entfernen. Für Vorstandskonten gilt Face ID bis 14 Tage nach der letzten Nextcloud-Anmeldung."},
    {"datum": "2026-11-21", "fuer": "alle",
     "titel": "Mehr entdecken auf der Terminseite",
     "text": "Unter jedem Termin stehen jetzt die nächsten Termine derselben Reihe und Vorschläge, die dich auch "
             "interessieren könnten. Mit „Frage zum Termin“ schreibst du dem Verein direkt eine Mail. Bei Terminen "
             "draußen zeigt die Seite in der Woche davor die Wettervorhersage, und nach dem Termin sehen Gäste unter "
             "„So war's“ die freigegebenen Fotos."},
    {"datum": "2026-11-21", "fuer": "vorstand",
     "titel": "Häkchen „Findet draußen statt“",
     "text": "Beim Anlegen und Bearbeiten eines Termins gibt es das Häkchen „Findet draußen statt“. Dann zeigt die "
             "Terminseite in den 7 Tagen davor die Wettervorhersage für euren Vereinsort. Für „So war's“ zählen nur "
             "Fotos, die im Fotoarchiv für die Veröffentlichung und für Social Media freigegeben sind."},
    {"datum": "2026-11-21", "fuer": "alle",
     "titel": "Schichten mit Vornamen",
     "text": "In der Terminliste steht bei Schichten jetzt der Vorname statt der Initialen. Gibt es den Vornamen "
             "mehrmals, kommt der Anfang des Nachnamens dazu, z. B. „Anna M.“."},
    {"datum": "2026-11-21", "fuer": "alle",
     "titel": "Terminliste lädt schneller",
     "text": "Die Terminliste zeigt zuerst die nächsten rund 20 Termine. Weitere holst du mit „Weitere Termine "
             "anzeigen“ unten in der Liste dazu. Suchst oder filterst du, siehst du wie bisher gleich alle Treffer."},
    {"datum": "2026-11-20", "fuer": "alle",
     "titel": "Anmelden mit Face ID oder Fingerabdruck",
     "text": "Unter „Ich“ kannst du jetzt Face ID, Touch ID oder deinen Fingerabdruck einrichten. "
             "Danach meldest du dich auf der Anmeldeseite mit einem Tipp auf „Mit Face ID / Fingerabdruck "
             "anmelden“ an, ganz ohne Passwort. Wer sich sonst über Nextcloud anmeldet, macht das "
             "mindestens alle 90 Tage einmal, damit die eigenen Rechte aktuell bleiben."},
    {"datum": "2026-11-19", "fuer": "vorstand",
     "titel": "Fotos aus der Galerie löschen",
     "text": "Fotos lassen sich jetzt auch in der Vollbildansicht (🗑) und im Fotoarchiv der "
             "Öffentlichkeitsarbeit („🗑 Löschen“) endgültig entfernen. Mitglieder können ihre eigenen "
             "Fotos löschen, der Vorstand jedes. Wurde ein Foto als Beitragsbild genutzt, verschwindet "
             "es dort mit."},
    {"datum": "2026-11-19", "fuer": "alle",
     "titel": "Neue Terminliste auf dem Handy",
     "text": "Auf dem Handy ist die Terminliste jetzt kompakter: Überschriften wie „Heute · Do, 19. Nov.“ "
             "bleiben beim Scrollen stehen, jede Karte zeigt ein kleines Bild und ist zart in der Farbe ihrer "
             "Kategorie getönt. Highlights wischst du seitwärts durch. Teilen, Merken und – wo möglich – "
             "Anmelden erreichst du mit einem Tipp, die Suche öffnet die Lupe. Oben bleibt eine schmale Leiste "
             "mit dem Herz deines Merkzettels, „App installieren“ findest du jetzt ganz unten."},
    {"datum": "2026-11-19", "fuer": "vorstand",
     "titel": "Die App so sehen, wie Gäste sie sehen",
     "text": "Unten in der Mitglieder-App gibt es für den Vorstand den Knopf „Als Gast ansehen“. Damit siehst "
             "du Termine und Startseite genau so wie Besucher:innen eurer Website, interne Termine erscheinen "
             "dort nur als „Raum belegt“. Mit „Beenden“ bist du wieder in deiner gewohnten Ansicht."},
    {"datum": "2026-11-19", "fuer": "vorstand",
     "titel": "Kategorien zu Gruppen zusammenfassen",
     "text": "Unter Termine → Kategorien könnt ihr jetzt jeder Kategorie eine Gruppe geben, "
             "zum Beispiel „Spiele“ für Spielezeit, Skat und Doppelkopf. In der Terminliste "
             "steht dann nur noch ein Knopf „Spiele“, der alle zugehörigen Termine zeigt. Gäste "
             "sehen den Knopf für nicht-öffentliche Termine nicht mehr, und solche Termine "
             "erscheinen in der Liste und im Kalender ausgegraut. Am Computer läuft die "
             "Terminliste mit eingeschaltetem Website-Stil jetzt auch für angemeldete "
             "Mitglieder über die volle Breite."},
    {"datum": "2026-11-19", "fuer": "alle",
     "titel": "Terminliste: schneller finden und dabei sein",
     "text": "Über der Terminliste gibt es jetzt Knöpfe für „Heute“, „Dieses Wochenende“ und "
             "„Nächste 7 Tage“, für jede Kategorie und für Zielgruppen wie Familien oder "
             "„barrierefrei“. Die Karten zeigen „Heute“ oder „Morgen“, Hinweise wie „Beginnt in "
             "2 Std.“, den Eintritt und – am Computer – Knöpfe für Kalender und Wegbeschreibung. Auf der "
             "Merkzettel kannst du dich am Vortag um 17 Uhr erinnern lassen."},
    {"datum": "2026-11-19", "fuer": "vorstand",
     "titel": "Neu beim Termin: Eintritt, Zielgruppen und Kategoriebilder",
     "text": "Beim Anlegen und Bearbeiten wählt ihr jetzt „Eintritt frei“ oder „Spende erwünscht“ "
             "und hakt an, für wen ein Termin gedacht ist (Familien, Kinder, Jugendliche, "
             "Senior:innen, barrierefrei, auch auf Englisch) und tragt bei Bedarf „Einlass ab“ und „Gut zu wissen“ ein – "
             "alles erscheint im neuen Faktenkasten der Terminseite. Unter Termine → Kategorien könnt ihr "
             "je Kategorie ein Standardbild hochladen – es erscheint bei allen Terminen ohne eigenes "
             "Bild. Regelmäßige Termine wie die Offene Tür sehen Gäste als eine Karte mit Wochenplan."},
    {"datum": "2026-11-19", "fuer": "vorstand",
     "titel": "⭐ Veranstaltungen als Highlight hervorheben",
     "text": "Beim Anlegen und Bearbeiten eines Termins gibt es den Haken „⭐ Als Highlight "
             "hervorheben“ und eine kurze Zeile wie „Saisonabschluss!“. Highlights stehen "
             "groß oben in der Terminliste und auf der Startseite der "
             "App, die Website kann sie automatisch übernehmen. Nach dem Termin endet das "
             "Highlight von selbst. Gäste sehen interne Termine nie als Highlight."},
    {"datum": "2026-11-19", "fuer": "alle",
     "titel": "♥ Veranstaltungen merken",
     "text": "Bei jedem Termin gibt es jetzt ein Herz. Tippst du darauf, landet die "
             "Veranstaltung auf deinem Merkzettel – oben im Kopf beim ♥. Noch einmal "
             "tippen nimmt sie wieder herunter. Gäste brauchen dafür ein kostenloses "
             "Freundekonto, die App erklärt beim ersten Herz, wie das geht. Die Zahl am Herz "
             "zeigt, wie viele gemerkte Termine noch bevorstehen. Neu ist auch der Teilen-Knopf "
             "direkt in der Terminliste – für WhatsApp, Signal, E-Mail oder als kopierter Link."},
    {"datum": "2026-11-19", "fuer": "vorstand",
     "titel": "App am Computer im Stil eurer Website",
     "text": "Unter Einstellungen → Mitglieder-App gibt es einen neuen Schalter: Gäste, "
             "die ohne Anmeldung am Computer in die App kommen, zum Beispiel über den "
             "Terminlink eurer Website, sehen sie dann im Kleid eurer Website, mit weißem "
             "Kopf, der beim Scrollen schmaler wird, einem Titelbild mit eigener Überschrift, "
             "Terminkarten mit Bild, Kurztext und Anmeldeknopf über die volle Breite und "
             "einem Link zurück zur Website. Mitglieder und alle Handys sehen die App wie "
             "gewohnt. Neu für alle: Die Datumssuche beginnt jetzt automatisch bei heute, "
             "und das Feld für den Trailer-Link nimmt auch eine Hörprobe auf – außer bei "
             "Kino-Terminen heißt der Knopf dann „▶ Hörprobe anhören“. Besucher:innen ohne "
             "Anmeldung sehen keine genauen Platzzahlen mehr, nur noch „Plätze frei“, „Nur noch "
             "wenige Plätze“ oder „Ausgebucht“. "
             "Die Termine pflegt ihr weiterhin nur in der App."},
    {"datum": "2026-11-18", "fuer": "vorstand",
     "titel": "Neues Feld „Mitwirkende“ bei Terminen",
     "text": "Beim Anlegen und Bearbeiten eines Termins könnt ihr jetzt eintragen, wer "
             "mitwirkt, zum Beispiel Referent:innen, eine Band oder ein Ensemble. Die Namen "
             "erscheinen auf der öffentlichen Terminseite und helfen Google, eure "
             "Veranstaltung richtig anzuzeigen. Bei Terminen „Nur für Mitglieder“ und "
             "„nicht-öffentlich“ bleiben sie verborgen."},
    {"datum": "2026-11-17", "fuer": "vorstand",
     "titel": "Eure öffentlichen Termine bei Google",
     "text": "Ist die Gastansicht der App an, finden Suchmaschinen eure öffentlichen "
             "Termine jetzt deutlich besser: Jede Veranstaltung hat eine sprechende "
             "Adresse, eine Kurzbeschreibung und Angaben zu Datum, Ort und Eintritt, "
             "die Google in der Veranstaltungs-Suche zeigen kann. Termine „Nur für "
             "Mitglieder“ und die Kategorie „nicht-öffentlich“ bleiben immer draußen. "
             "Abschalten könnt ihr das unter Einstellungen → Mitglieder-App."},
    {"datum": "2026-11-16", "fuer": "vorstand",
     "titel": "Mehr Sicherheit bei Links, Bildern und Kalender-Abos",
     "text": "Die App prüft jetzt jede Adresse, die ihr für Kalender-Abos, Bilder "
             "oder die Vereins-Cloud eintragt, und lädt nur noch von öffentlichen "
             "Servern. Gespeicherte Mailvorlagen und Links werden vor der Anzeige "
             "von versteckten Skripten gesäubert. Für euch ändert sich nichts – es "
             "schützt eure Daten und eure Besucher:innen."},
    {"datum": "2026-11-16", "fuer": "alle", "module": ["verteilen"],
     "titel": "Verteilaktionen: persönliche Zustellungen an Kontakte",
     "text": "Eine Verteilaktion kann jetzt auch bestimmten Kontakten etwas "
             "persönlich in den Briefkasten bringen – etwa eine Einladung an "
             "Sponsor:innen oder Partner:innen. In der App gibt es dafür den "
             "Reiter „📬 Zustellen“: Ihr übernehmt eine Straße und seht erst "
             "dann Namen und Hausnummern, lasst euch einen Weg vorschlagen und "
             "hakt jede Anschrift ab. Klappt etwas nicht (verzogen, kein "
             "Briefkasten), meldet ihr das mit einem Tipp – der Vorstand prüft "
             "dann die Anschrift."},
    {"datum": "2026-11-16", "fuer": "alle", "module": ["verteilen"],
     "titel": "Verteilaktionen: Flyer verteilen und Plakate aufhängen",
     "text": "Flyer, Programmhefte und Postkarten verteilt ihr jetzt gemeinsam "
             "über die App: Straße übernehmen, auf der Karte sehen, was schon "
             "erledigt ist, und euch einen Rundgang passend zu eurer Zeit "
             "vorschlagen lassen – mit Fußweg auf der Karte. Gibt es mehrere "
             "Flyer, wählt ihr vorher aus, welche ihr dabeihabt. Plakate setzt "
             "ihr direkt auf der Karte; jedes bekommt eine Nummer. Bei der "
             "Plakat-Runde (auch im Auto als Beifahrer:in) piept die App in der "
             "Nähe eines Plakats und fragt „Alles ok?“. Euer Standort bleibt "
             "dabei auf dem Handy. Der Vorstand legt alles in der Verwaltung "
             "unter Termine → Verteilaktionen an."},
    {"datum": "2026-11-09", "fuer": "vorstand", "module": ["contenthub"],
     "titel": "Content-Hub im ÖA-Cockpit",
     "text": "Unter Öffentlichkeit → Content-Hub seht ihr, was der Hub aus euren "
             "Veranstaltungen macht: Beiträge für Instagram, Facebook, die App und "
             "Signal-Gruppen. Freigeben kann jede:r aus Vorstand und "
             "Öffentlichkeitsarbeit – nie den eigenen Beitrag. Fotos gehen nur "
             "mit dem neuen Social-Media-Häkchen des Vorstands raus, und für den "
             "WhatsApp-Kanal und Facebook-Gruppen gibt es fertige Texte zum "
             "Kopieren. Wer im Urlaub ist, trägt dort eine Vertretung ein."},
    {"datum": "2026-11-07", "fuer": "alle",
     "titel": "Anmeldung über das Vereins-Konto hakt nicht mehr",
     "text": "Der Login über Nextcloud wurde umgebaut: Die Warteseite übersteht "
             "jetzt ein Neuladen, der Anmelde-Knopf und die automatische "
             "Weiterleitung arbeiten mit demselben Anmelde-Schlüssel, und wenn "
             "die App zwischendurch neu startet, wird die Anmeldung beim "
             "nächsten Öffnen automatisch zu Ende gebracht – kein Hängen, kein "
             "Von-vorn-Anfangen. Außerdem gilt „Eingeloggt bleiben“ jetzt auch "
             "für den Vorstand: einen Monat lang, wenn das Häkchen beim "
             "Anmelden gesetzt ist (Mitglieder weiterhin 180 Tage)."},
    {"datum": "2026-11-07", "fuer": "alle",
     "titel": "Wochentag an allen Terminen",
     "text": "Termine zeigen jetzt überall den Wochentag als Kurzform – "
             "„Sa, 07.11.2026“ statt nur „07.11.2026“: in der Verwaltung "
             "(Kalenderliste, Veranstaltungen, Schichtplan, Serien) und in "
             "der Mitglieder-App (Terminliste, Termin-Seite, Startseite, "
             "Kino, Schichten). So ist auf einen Blick klar, ob etwas aufs "
             "Wochenende fällt."},
    {"datum": "2026-11-06", "fuer": "alle", "module": ["schichten"],
     "titel": "Schichten: Mitteilungen bei Änderungen + sichereres Speichern",
     "text": "Wer eingeteilt ist, bekommt jetzt eine Mitteilung, wenn die "
             "Schicht zeitlich verschoben, der Platz entfernt oder die ganze "
             "Schicht gestrichen wird – und die Schicht-Erinnerung läuft nach "
             "einer Verschiebung neu. Der Vorstand erfährt per Mitteilung, "
             "wenn sich jemand austrägt. Beim Sammel-Speichern in der "
             "Termin-Maske werden frische Eintragungen aus der App nicht mehr "
             "überschrieben, und niemand fällt mehr still aus einem Platz, "
             "nur weil er nicht in der Auswahlliste steht. Rückmeldungen zu "
             "Schichten sehen jetzt nur noch der Vorstand und die Person "
             "selbst."},
    {"datum": "2026-11-06", "fuer": "vorstand", "module": ["schichten"],
     "titel": "Schichten nachträglich bearbeiten + Zeit-Vorschlag",
     "text": "Titel, Datum, Uhrzeit, Ort und Notiz einer Schicht lassen sich "
             "jetzt nach dem Anlegen ändern (Schicht öffnen → „Schicht "
             "bearbeiten“; die Plätze samt Besetzung bleiben erhalten). Auch "
             "die Rolle eines einzelnen Platzes kannst du nachträglich "
             "wechseln, ohne die eingeteilte Person zu verlieren. Neue "
             "Schichten bekommen als Zeitvorschlag automatisch 15 Minuten vor "
             "Terminbeginn bis 15 Minuten nach Terminende. Wenn du eine "
             "Veranstaltung auf ein anderes Datum verschiebst, wandern ihre "
             "Schichten jetzt mit. Über der Schichten-Liste gibt es eine "
             "Such- und Filterleiste (unbesetzt, Tausch, voll besetzt, "
             "kommend, vergangen – und je Veranstaltungs-Kategorie); ein "
             "Klick auf eine Spaltenüberschrift sortiert die Liste. Und das "
             "Anlegen ist schlanker: In der Termin-Maske genügt es, bei "
             "„Neue Schicht“ eine Rolle zu wählen – ihr Name wird als Titel "
             "übernommen, die Plätze bekommen die Rolle gleich mit."},
    {"datum": "2026-11-06", "fuer": "vorstand", "module": ["adventskalender"],
     "titel": "Adventskalender: eigene Verwaltungsseite mit Archiv",
     "text": "Im Menü unter Termine → Adventskalender findest du jetzt alles an "
             "einem Ort: die Türchen der Saison mit Anmeldezahlen und "
             "Bearbeiten-Links, den Einleitungstext der öffentlichen Seite zum "
             "Selbst-Ändern und ein Archiv vergangener Jahrgänge – je Jahr ein "
             "Bild, ein Rückblickstext und die Teilnehmerzahl, als PDF im "
             "Vereins-Briefkopf exportierbar. Der ganze Bereich ist ein Modul "
             "und lässt sich je Verein in den Einstellungen ein- und "
             "ausschalten. Außerdem gilt auf der öffentlichen Seite: Der "
             "Hinweis „Anmeldung per E-Mail“ erscheint nur noch, wenn die "
             "Anmeldung am Termin wirklich eingeschaltet ist – Türchen mit "
             "bloßem Eintritt oder Platzgrenze laden zum einfachen "
             "Vorbeikommen ein."},
    {"datum": "2026-11-05", "fuer": "vorstand",
     "titel": "Veranstaltungsbild direkt in der Termin-Maske",
     "text": "Beim Anlegen und Bearbeiten eines Termins kannst du jetzt ein "
             "Bild hochladen, von einem Link übernehmen, austauschen oder "
             "entfernen – mit Vorschau. Das Bild wird automatisch auf "
             "Webgröße verkleinert und erscheint sofort überall: auf der "
             "Termin-Seite, der Anmeldeseite, beim Teilen und im "
             "Adventskalender."},
    {"datum": "2026-11-04", "fuer": "vorstand", "module": ["schichten"],
     "titel": "Rollen sind jetzt vor Karteileichen geschützt",
     "text": "Eine Rolle, die noch in kommenden Schichten steckt, lässt sich "
             "nicht mehr löschen – die App sagt, wie viele Schichten betroffen "
             "sind. Umbenennen ist dagegen jederzeit sicher: Die Schichten "
             "übernehmen den neuen Namen automatisch, nichts bricht ab."},
    {"datum": "2026-11-04", "fuer": "alle", "module": ["schichten"],
     "titel": "Schichten: Rolle ansehen, dann übernehmen",
     "text": "Jede Schicht hat jetzt einen „Rolle“-Knopf (bei deinen eigenen "
             "Schichten „meine Rolle“): Er klappt direkt unter der Schicht auf, "
             "welche Aufgaben dazugehören – und der Übernehmen-Knopf bleibt "
             "daneben. So weißt du vor dem Zusagen, was dich erwartet. Alle "
             "Rollen-Erklärungen findest du weiterhin gesammelt unten auf der "
             "Schichten-Seite."},
    {"datum": "2026-11-04", "fuer": "alle", "module": ["veranstaltungen"],
     "titel": "Anmeldestand für Kooperationspartner:innen und Sponsor:innen",
     "text": "Veranstaltungen mit eingetragener Unterstützung zeigen auf der "
             "öffentlichen Anmeldeseite jetzt einen Link „Aktueller Anmeldestand“: "
             "eine Seite nur mit Zahlen – angemeldete Personen, freie Plätze, "
             "Warteliste –, die ihr euren Partner:innen weitergeben könnt. "
             "Namen oder Kontaktdaten stehen dort nie."},
    {"datum": "2026-11-04", "fuer": "alle",
     "titel": "Adventskalender-Seite: Band mit allen Unterstützer-Logos",
     "text": "Unter den Türchen läuft jetzt ein Band mit den Logos und Namen "
             "aller Kooperationspartner:innen und Sponsor:innen der Saison – "
             "wer beim Termin eine Unterstützung einträgt, erscheint automatisch. "
             "Das Band lässt sich per Knopf anhalten."},
    {"datum": "2026-11-04", "fuer": "vorstand", "module": ["veranstaltungen"],
     "titel": "Fotoerlaubnis nennt jetzt auch den WhatsApp-Kanal",
     "text": "Einwilligung Stufe 2 heißt jetzt „Facebook, Instagram und "
             "WhatsApp-Kanal“ – im Anmeldeformular, in der Druckvorlage, im "
             "Einzel-PDF und in der Anfrage-Mail. Wichtig: Einwilligungen von "
             "vor dem 4. November decken WhatsApp nicht ab – vor Kanal-Posts "
             "kurz aufs Antwortdatum schauen."},
    {"datum": "2026-11-04", "fuer": "alle",
     "titel": "Die App nutzt jetzt auch große Bildschirme",
     "text": "Am Computer oder am Tablet quer macht sich die Mitglieder-App "
             "jetzt breit: Das Menü wandert als Leiste nach oben, die "
             "Startseite zeigt ihre Karten mehrspaltig, bei den Terminen "
             "stehen die Filter als Seitenspalte neben der Liste und die "
             "Termin-Seite stellt Infos und Mitmach-Knöpfe nebeneinander. "
             "Auf dem Handy bleibt alles wie gewohnt – es gibt nichts "
             "umzustellen, die App passt sich von selbst an."},
    {"datum": "2026-11-04", "fuer": "vorstand",
     "titel": "Verwaltung am Handy: Rundmail & Co. stapeln sauber",
     "text": "Einige Verwaltungsseiten waren auf dem Handy zu breit – "
             "Rundmail schreiben, Serienbriefe, der Logo-Zuschnitt und die "
             "Pressearbeit ordnen ihre Spalten jetzt sauber untereinander. "
             "Große Tabellen wie Kassenbuch, Mitglieder- und Beitragsliste "
             "lassen sich bei Platzmangel seitlich wischen, und der "
             "Abmelde-Knopf ist am Handy wieder sichtbar."},
    {"datum": "2026-11-04", "fuer": "alle",
     "titel": "Der Lebendige Adventskalender hat eine eigene Seite",
     "text": "Unter /adventskalender – einfach an die Adresse eurer "
             "Vereinsverwaltung angehängt – zeigt eine festliche Seite alle "
             "Türchen des Lebendigen Adventskalenders: mit Preis, Anmelde-Knopf "
             "und den freien Plätzen, immer aktuell aus der App. Das nächste "
             "Türchen steht oben und glitzert, vergangene rutschen ans Ende. "
             "Die Seite braucht keine Anmeldung – teilt sie gern. Sie erscheint "
             "automatisch, sobald es eine Termin-Kategorie mit „Adventskalender“ "
             "im Namen gibt; eure Kanäle pflegt ihr in den Einstellungen "
             "(Instagram, WhatsApp-Kanal)."},
    {"datum": "2026-10-27", "fuer": "alle",
     "titel": "Termine teilen – jetzt mit Veranstaltungsbild",
     "text": "Der Knopf 📤 Teilen auf der Termin-Seite legt das "
             "Veranstaltungsbild jetzt mit ins Teilen-Blatt: In WhatsApp, "
             "Signal und Co. kommt der Termin als Bild mit Text und Link an. "
             "Auch wer nur den Link verschickt, bekommt eine Vorschau mit "
             "Titel, Datum, Ort und Bild. Nicht-öffentliche Termine bleiben "
             "dabei wie gewohnt geschützt – sie zeigen nach außen weder Namen "
             "noch Bild."},
    {"datum": "2026-10-05", "fuer": "vorstand",
     "titel": "Vereinsdokumente öffnen sich wieder im eingebetteten Betrachter",
     "text": "Seit dem 20. September blieb die Ansicht beim Öffnen hochgeladener "
             "Vereinsdokumente leer – eine Sicherheits-Verschärfung hatte "
             "versehentlich auch die hauseigene Dokument-Ansicht ausgesperrt. "
             "Das ist repariert: PDFs und Bilder erscheinen wieder direkt im "
             "Browser. Der Schutz vor gefährlichen Dateitypen (etwa "
             "hochgeladenen HTML-Dateien) bleibt dabei vollständig bestehen."},
    {"datum": "2026-10-05", "fuer": "vorstand",
     "titel": "Druckvorlagen für die Kasse: Einzahlungsbeleg, Zählprotokoll, Auslagenerstattung",
     "text": "Im neuen Menüpunkt Finanzen → 🖨️ Vorlagen gibt es drei "
             "PDF-Vorlagen: einen Einzahlungsbeleg für Bareinzahlungen "
             "(zwei Belege je Seite, bis zu vier eurer Geldkonten stehen zum "
             "Ankreuzen drauf), ein Zählprotokoll für den Kassensturz im "
             "Vier-Augen-Prinzip und einen Antrag auf Auslagenerstattung – "
             "inklusive Verzichts-Option für eine Aufwandsspende. Alle "
             "Vorlagen lassen sich direkt am Bildschirm ausfüllen oder "
             "drucken und tragen automatisch euren Vereinsnamen, "
             "Zählprotokoll und Erstattungsantrag auch euer Logo."},
    {"datum": "2026-09-20", "fuer": "vorstand",
     "titel": "Kontoauszüge hochladen und nach Geschäftsjahr archivieren",
     "text": "Unter Finanzen → 📄 Kontoauszüge legst du die Auszüge deiner "
             "Geldkonten ab (PDF aus dem Online-Banking oder Foto) – sauber "
             "sortiert nach Geschäftsjahr. Kassenprüfer:innen können damit "
             "alle Buchungen nachvollziehen, und der erste Auszug des Jahres "
             "belegt den Anfangsbestand im Kassenbericht. Ansehen und Löschen "
             "geht direkt aus der Liste."},
    {"datum": "2026-09-20", "fuer": "vorstand",
     "titel": "Kassenbuch gleicht sich mit der Bank ab",
     "text": "Beim Umsatz-Abruf merkt sich die App jetzt auch den Kontostand, "
             "den die Bank meldet. Die Kassenstand-Karte unter Finanzen zeigt "
             "dann direkt: grünes „stimmt mit der Bank überein“ – oder einen "
             "Warnhinweis mit der Differenz, meist unverbuchte Umsätze oder "
             "ein fehlender Anfangsbestand, jeweils mit Link zum richtigen "
             "Ort. So fällt jede Abweichung sofort auf statt erst bei der "
             "Kassenprüfung."},
    {"datum": "2026-09-20", "fuer": "vorstand",
     "titel": "Kategorie „nicht-öffentlich“ verbirgt Termine nach außen",
     "text": "Termine mit der Kategorie „nicht-öffentlich“ – etwa private "
             "Raumbuchungen – erscheinen für Gäste, Freund:innen und im "
             "Kalender-Abo nur noch als „Veranstaltung“: Datum, Uhrzeit und "
             "Ort bleiben sichtbar, damit klar ist, dass das Haus belegt ist; "
             "Name, Beschreibung, Bild und Sponsor bleiben privat. Die "
             "Kategorie legst du einmalig unter Kalender → Kategorien an. "
             "Mitglieder sehen weiterhin alles; der Haken „Nur für "
             "Mitglieder“ hat Vorrang."},
    {"datum": "2026-08-31", "fuer": "alle",
     "titel": "Dunkelmodus jetzt wirklich überall",
     "text": "Bisher blieben einige Seiten stur hell – etwa die Anmeldeseite "
             "zu Veranstaltungen, das Beitrittsformular, die Anmeldung "
             "(Login), Impressum & Datenschutz und die Dokument-Ansicht. "
             "Jetzt machen alle Seiten den Wechsel mit: Was du unter "
             "🎨 Aussehen einstellst, gilt durchgängig, und öffentliche "
             "Seiten folgen automatisch der Einstellung deines Geräts. Auch "
             "die Browserleiste färbt sich auf dem Handy passend dunkel."},
    {"datum": "2026-08-30", "fuer": "alle", "module": ["veranstaltungen"],
     "titel": "Anmelden wie bei den Profis",
     "text": "Rund um die Veranstaltungs-Anmeldung gibt es ein ganzes Paket: "
             "Auf der Warteliste siehst du jetzt deinen Platz („Du bist auf "
             "Platz 2“), der Knopf „📅 Zum Kalender hinzufügen“ legt den Termin "
             "ins Handy, über „✏️ Personen ändern“ auf dem Voucher meldest du "
             "selbst jemanden nach oder trägst eine Person aus – und für "
             "Mitglieder kann es einen eigenen, günstigeren Preis geben, der "
             "automatisch gilt, wenn du angemeldet bist."},
    {"datum": "2026-08-30", "fuer": "vorstand", "module": ["veranstaltungen"],
     "titel": "Storno-Frist, Nachrück-Angebot, Gutscheine, Einlass-Bericht",
     "text": "Für den Vorstand: Je Termin lassen sich jetzt eine Storno-Frist, "
             "ein Mitgliederpreis und das Nachrücken mit 24-Stunden-"
             "Bestätigungsfrist einstellen. Unter Anmeldungen gibt es "
             "Gutschein-/Freikartencodes für Presse und Sponsoren, die "
             "Anmeldeseite wirbt bei wenigen Restplätzen mit „🔥 Nur noch …“, "
             "und der neue 📊 Einlass-Bericht zeigt nach der Veranstaltung "
             "Erscheinungsquote, Ankunftswelle, Barkasse und No-Shows – samt "
             "Saison-Vergleich."},
    {"datum": "2026-08-30", "fuer": "vorstand", "module": ["veranstaltungen"],
     "titel": "Veranstaltungs-Vorlagen jetzt zentral – und im Vereins-Design",
     "text": "Alle E-Mail-Vorlagen wohnen jetzt an einem Ort: Unter Kommunikation "
             "→ Vorlagen findest du auch die Veranstaltungs-Vorlagen (Bestätigung, "
             "Erinnerung, Nachrücken u. a.) mit eigenem Filter-Chip. Und jede "
             "Veranstaltungs-Mail geht ab sofort im Vereins-Design raus – mit "
             "eurer Schriftart und der Vereins-Signatur samt Logo."},
    {"datum": "2026-08-30", "fuer": "vorstand", "module": ["veranstaltungen"],
     "titel": "Einlass mit QR-Scan und Ampel",
     "text": "An jeder Veranstaltung mit Anmeldung gibt es jetzt den Knopf "
             "„🎫 Einlass“: Voucher mit der Handykamera scannen, und die Ampel "
             "sagt sofort Bescheid – 🟢 alles klar, 🟡 bitte beachten (offener "
             "Betrag, Warteliste, schon eingecheckt), 🔴 erst klären. Abgehakt "
             "wird pro Person oder mit einem Tipp für die ganze Gruppe, bar "
             "Kassiertes markierst du direkt am Einlass als bezahlt. "
             "Teilnehmerliste, Excel und Detailseite zeigen, wer wann wirklich "
             "da war. Der Einlass geht auch aus der Mitglieder-App – für den "
             "Vorstand und alle, die an dem Termin eine Schicht übernommen "
             "haben, gern an mehreren Handys gleichzeitig – der Knopf steht "
             "auch direkt an der eigenen Schicht. Läuft der Einlass, zeigen "
             "Termin- und Anmeldeseite das öffentlich an; rund 10 Minuten "
             "nach Beginn schließt er sich von selbst, draußen steht dann "
             "„🔒 Der Einlass ist geschlossen“, und beim Scannen warnt die "
             "Ampel vor Nachzügler:innen – wieder öffnen kann ihn nur der "
             "Schalter."},
    {"datum": "2026-08-30", "fuer": "vorstand",
     "titel": "Pressemitteilung im Profi-Layout",
     "text": "Das PDF einer Pressemitteilung sieht jetzt aus wie vom Profi: "
             "Briefkopf mit Vereinslogo, Ort und Datum, Schlagzeile, fetter "
             "Vorspann – und das Beitragsbild steht am Ende in voller Breite, "
             "mit Bildunterschrift und Bildnachweis, die du je Mitteilung "
             "selbst eintragen kannst. Den Abschluss bilden Pressekontakt und "
             "Seitenzahlen. Redaktionen bekommen die Bilddatei in "
             "Druckqualität weiterhin zusätzlich als Anhang."},
    {"datum": "2026-08-30", "fuer": "vorstand",
     "titel": "Konten: Kategorien wieder im Blick",
     "text": "In den Umsatz-Tabellen (Bankabruf, PayPal, CSV-Import) zeigen "
             "bereits verbuchte Zeilen jetzt die Kategorie, mit der sie "
             "verbucht wurden – statt nur eines Strichs. Neu ist außerdem der "
             "Filter-Chip „🏷️ ohne Kategorie“ dort und im Kassenbuch: ein "
             "Tipp, und du siehst genau die Umsätze, die noch einzuordnen "
             "sind."},
    {"datum": "2026-08-30", "fuer": "vorstand",
     "titel": "Sende-Maske wie im Mailprogramm",
     "text": "Beim Verfassen stehen An, CC und BCC jetzt oben über dem "
             "Betreff – mit den gewählten Empfänger:innen als Chips und der "
             "Tippsuche direkt im An-Feld. Eine neue Adresse tippst du samt "
             "@ einfach ein und drückst die Eingabetaste. CC und BCC sind "
             "gedacht für ein Archiv- oder Vorstandspostfach, das jede "
             "Einzelmail in Kopie bekommt."},
    {"datum": "2026-08-30", "fuer": "vorstand",
     "titel": "Vorlagen nach Kategorie filtern",
     "text": "Auf der Vorlagen-Seite zeigt eine Filterleiste alle Kategorien "
             "mit Anzahl – ein Tipp auf einen Chip blendet nur diese "
             "Vorlagen ein, „Alle“ holt sie zurück."},
    {"datum": "2026-08-30", "fuer": "alle",
     "titel": "Veranstaltungen als Serie",
     "text": "Regelmäßige Termine legst du jetzt als Serie an – wöchentlich, "
             "alle N Wochen oder monatlich, mit Kollisionsprüfung und "
             "Ausweichvorschlägen vor dem Speichern. Serientermine tragen ein "
             "🔁, zeigen ihre Position in der Reihe, und beim Absagen wirst du "
             "gefragt: nur dieser Termin, alle folgenden oder die ganze Serie."},
    {"datum": "2026-08-30", "fuer": "vorstand",
     "titel": "Kalender-Import mit Auswahl",
     "text": "Der Import von auf-nach-mv.de zeigt jetzt erst eine Vorschau: "
             "🆕 neue, ✏️ zu ergänzende und ✔️ vollständige Veranstaltungen – "
             "du wählst aus, was übernommen wird. Nebenbei repariert: Kurze "
             "Anreißer-Texte werden jetzt zuverlässig durch die ausführliche "
             "Beschreibung ersetzt."},
    {"datum": "2026-08-30", "fuer": "vorstand",
     "titel": "Rundmails an Kontakte und freie Adressen",
     "text": "Die Sende-Maske erreicht jetzt drei Quellen: Mitglieder, eure "
             "Kontakte (nach Kategorien wie Presse oder Partner) und frei "
             "eingetragene Adressen – letztere auf Wunsch gleich als Kontakt "
             "gespeichert. Dazu: Tippsuche über alle Empfänger:innen, Chips "
             "der Auswahl, und die Schnellauswahl ergänzt statt zu ersetzen."},
    {"datum": "2026-08-30", "fuer": "vorstand",
     "titel": "E-Mail-Vorlagen mit Kategorien",
     "text": "Die Vorlagen sind jetzt geordnet: getrennt nach vereinsinterner "
             "und externer Kommunikation, darunter frei benennbare Kategorien. "
             "Zuordnen und Kategorien pflegen geht direkt auf der "
             "Vorlagen-Seite; die Auswahl beim Verfassen ist entsprechend "
             "gruppiert."},
    {"datum": "2026-08-30", "fuer": "vorstand",
     "titel": "Bank-Umsätze filtern und blättern",
     "text": "Beim Verbuchen abgerufener Umsätze kannst du jetzt nach "
             "Einnahmen oder Ausgaben filtern und in Seiten zu 10, 20 oder 50 "
             "blättern. Verbucht wird nur, was gerade sichtbar ist – "
             "weggefilterte Zeilen bleiben unangetastet."},
    {"datum": "2026-08-30", "fuer": "vorstand",
     "titel": "Aktivitäten zeigen, wer es war",
     "text": "Die letzten Aktivitäten nennen jetzt die Person, die den "
             "Eintrag angelegt hat – auf der Kachel und im vollständigen "
             "Verlauf, der jetzt seitenweise blätterbar ist."},
    {"datum": "2026-08-30", "fuer": "alle",
     "titel": "Nextcloud-Anmeldung verzeiht Tippfehler",
     "text": "Nach einem falschen Passwort führte der Anmeldelink bisher in "
             "eine Sackgasse („Zugriff verboten“) – jetzt holt er bei jedem "
             "Klick einen frischen Anlauf, und die Warteseite macht einfach "
             "weiter."},
    {"datum": "2026-08-30", "fuer": "alle",
     "titel": "Kurzanleitungen zum Lesen statt Zoomen",
     "text": "Die Kurzanleitungen öffnen sich in der Hilfe jetzt als gut "
             "lesbare Textansicht fürs Handy – mit „Zurück“, „Nächste "
             "Anleitung“ und „Teilen“. Das PDF (mit Versionsnummer und "
             "Kontaktadresse) gibt es weiter zum Ausdrucken und Weitergeben."},
    {"datum": "2026-08-30", "fuer": "alle",
     "titel": "Handbuch und Kurzanleitungen in der Hilfe",
     "text": "Unter „Hilfe“ findest du jetzt das Handbuch und die Kurz"
             "anleitungen eures Vereins als PDF – zugeschnitten auf das, "
             "was ihr wirklich nutzt. Die Anleitung für deine Rolle steht "
             "dabei ganz oben."},
    {"datum": "2026-08-30", "fuer": "vorstand",
     "titel": "Mitglieder über Neuerungen informieren",
     "text": "An jedem Eintrag dieser Seite gibt es in der Verwaltung den "
             "Knopf „Mitglieder informieren“: Er schickt eine Push-Mitteilung "
             "über das Thema „Neuigkeiten“ – bewusst nur auf Knopfdruck, "
             "damit niemand ungefragt überschüttet wird."},
    {"datum": "2026-08-30", "fuer": "alle",
     "titel": "Neu hier: „Was ist neu“",
     "text": "Genau diese Seite: Ab jetzt findest du hier in Klartext, was die "
             "App dazugelernt hat. Gibt es etwas Neues, erscheint auf der "
             "Startseite von selbst eine Hinweis-Kachel – einmal angeschaut, "
             "verschwindet sie wieder."},
    {"datum": "2026-08-30", "fuer": "vorstand",
     "titel": "Aktivitäten-Verlauf zum Durchblättern",
     "text": "Die Dashboard-Kachel „Letzte Aktivitäten“ hat jetzt den Knopf "
             "„Alle anzeigen“: Dahinter liegt der vollständige Verlauf – nach "
             "Tagen gruppiert, jeder Eintrag führt direkt in seinen Bereich."},
    {"datum": "2026-08-30", "fuer": "alle",
     "titel": "Rundmails kommen nur noch einmal an",
     "text": "Wer mehrere E-Mail-Adressen hinterlegt hat, bekommt Rundmails "
             "jetzt nur noch an die Hauptadresse. Welche das ist, wählst du "
             "selbst in der App unter „Ich → Meine Daten“."},
    {"datum": "2026-08-30", "fuer": "vorstand",
     "titel": "Vereins-Signatur und persönliche Signatur getrennt",
     "text": "Die Vereins-Signatur unter Rundmails ist jetzt für alle gleich. "
             "Wer mit der eigenen Adresse sendet, kann unter „Mein Absender“ "
             "zusätzlich eine persönliche Signatur pflegen – sie erscheint "
             "über der Vereins-Signatur."},
    {"datum": "2026-08-29", "fuer": "vorstand",
     "titel": "Handbuch und Kurzanleitungen passend zum Verein",
     "text": "Das Handbuch und die fünf Kurzanleitungen gibt es jetzt je "
             "Verein: mit eigenem Logo und Namen, und ohne Kapitel zu "
             "Funktionen, die euer Verein gar nicht nutzt."},
    {"datum": "2026-08-19", "fuer": "alle",
     "titel": "Du entscheidest, worüber dich die App benachrichtigt",
     "text": "Unter „Ich → Datenschutz → Meine Mitteilungen“ wählst du die "
             "Themen deiner Push-Mitteilungen selbst – Termine, Schichten, "
             "Neuigkeiten und mehr. Standard ist: alles an."},
    {"datum": "2026-08-19", "fuer": "alle", "module": ["veranstaltungen"],
     "titel": "Deine Fotoerlaubnis, deine Entscheidung",
     "text": "Bei jeder Veranstaltung entscheidest du selbst, ob der Verein "
             "Fotos von dir zeigen darf – getrennt für die Vereinsarbeit und "
             "für Facebook/Instagram. Ansehen, ändern und widerrufen geht "
             "jederzeit in der App, auch nach der Veranstaltung."},
    {"datum": "2026-08-17", "fuer": "vorstand", "module": ["veranstaltungen"],
     "titel": "Fotoerlaubnisse einholen leicht gemacht",
     "text": "Wer bei einer Anmeldung nicht geantwortet hat, bekommt auf Knopf"
             "druck eine freundliche Nachfrage-Mail mit persönlichem Link. "
             "Dazu gibt es eine rechtssichere Druckvorlage, ein PDF je Person "
             "und den Upload des unterschriebenen Formulars als Nachweis."},
    {"datum": "2026-08-17", "fuer": "vorstand",
     "titel": "SEPA-Lastschrift als runder Ablauf",
     "text": "Der Beitragseinzug ist jetzt ein geführter Vorgang: Vorprüfung "
             "mit Ampelliste, automatische Vorabankündigung per E-Mail, "
             "Sammelverbuchung nach dem Einzug und Erfassung von Rückläufern – "
             "statt einer nackten Datei und hundert Einzelklicks."},
    {"datum": "2026-08-06", "fuer": "vorstand",
     "titel": "Bankzugang wird persönlich",
     "text": "Jede Person im Vorstand meldet sich mit den eigenen Bankdaten "
             "an; dank Gerätebindung fragt die Bank nur noch etwa alle 90 Tage "
             "nach einer TAN. Der Verein behält nur das Konto selbst."},
    {"datum": "2026-07-29", "fuer": "vorstand",
     "titel": "Mails mit persönlichem Absender",
     "text": "Unter „Einstellungen → Mein Absender“ hinterlegst du deine "
             "eigene Mailadresse. Beim Versand wählst du dann: als Verein "
             "oder mit deinem Namen und deiner Adresse."},
]


def fuer_anzeige(einst, vorstand=False, seit=None):
    """Die sichtbaren Einträge für diesen Verein und diese Rolle, neueste zuerst.

    seit: ISO-Datum/Zeitstempel – nur Einträge NACH diesem Zeitpunkt (für
    „neu seit deinem letzten Besuch“); None = alle."""
    sichtbar = []
    for e in EINTRAEGE:
        if e["fuer"] == "vorstand" and not vorstand:
            continue
        try:
            if any(not einst["modul_" + m] for m in e.get("module", [])):
                continue
        except (KeyError, IndexError, TypeError):
            continue  # unbekanntes Modulfeld → Eintrag lieber verstecken
        if seit and e["datum"] <= str(seit)[:10]:
            continue
        sichtbar.append(e)
    return sorted(sichtbar, key=lambda e: e["datum"], reverse=True)


def anzahl_neu(einst, vorstand=False, seit=None):
    """Wie viele Einträge seit dem letzten Besuch dazugekommen sind."""
    return len(fuer_anzeige(einst, vorstand=vorstand, seit=seit))
