"""Startbestückung der Fördertopf-Datenbank.

Beim ersten Aufruf des Fördermittel-Bereichs bekommt jeder Verein eine Liste
recherchierter Förderprogramme, die für gemeinnützige Vereine – besonders in
Mecklenburg-Vorpommern – tatsächlich in Frage kommen. Danach ist die Liste ganz
normal bearbeitbar: Was der Verein löscht oder ausblendet, kommt NICHT wieder
(Versions-Flag `vereine.foerder_seed`, gleiches Muster wie `vorlagen_texte`).

Die Angaben wurden im Juli 2026 auf den Seiten der Geldgeber geprüft. Trotzdem
gilt: Fristen und Summen ändern sich jährlich – verbindlich ist immer die
verlinkte Webseite. Wo ein Programm gerade geschlossen ist, steht das offen im
Fristen-Feld, damit niemand vergeblich Arbeit hineinsteckt.
"""
import logging

from .db import now_iso

SEED_VERSION = 2

# (name, traeger, url, was, bereiche, summe_von, summe_bis, eigenanteil,
#  fristen, zielgruppe, region, kontakt, notiz, favorit)
PROGRAMME = [
    # ── Die naheliegendsten Töpfe für einen MV-Verein (vorgemerkt) ─────────
    ("DSEE Mikroförderprogramm – Ehrenamt gewinnen. Engagement binden.",
     "Deutsche Stiftung für Engagement und Ehrenamt (DSEE)",
     "https://www.deutsche-stiftung-engagement-und-ehrenamt.de/foerderung/mikrofoerderprogramm/",
     "Niedrigschwellige Förderung für ehrenamtlich getragene Vereine: Danke-Veranstaltungen, "
     "Öffentlichkeitsarbeit, Workshops, kleinere Anschaffungen, Honorare. Der einfachste Einstieg "
     "überhaupt – wenige Seiten Antrag.",
     "Ehrenamt & Engagement,Gesellschaft & Zusammenhalt,Kultur & Begegnung",
     0, 1500, "mind. 10 % (max. 90 % der Ausgaben)",
     "laufend, ganzjährig – Projekt muss im selben Kalenderjahr enden",
     "gemeinnützige, überwiegend ehrenamtlich getragene Vereine in ländlichen/strukturschwachen "
     "Regionen (MV weitgehend förderfähig); max. 1.500 € je Verein und Jahr",
     "bundesweit", "",
     "Wenn ihr nur einen Antrag im Jahr stellt, dann diesen. Die Stiftung sitzt in Neustrelitz – "
     "kurze Wege, und MV ist ausdrücklich Zielregion.", 1),

    ("Aktion Mensch – Sport und Kultur vor Ort", "Aktion Mensch e. V.",
     "https://www.aktion-mensch.de/foerderung/foerderangebote/sport-kultur",
     "Inklusive, leicht zugängliche Sport- und Kulturangebote im Quartier, die Menschen mit "
     "Behinderung aktiv und gleichberechtigt einbeziehen.",
     "Kultur & Begegnung,Gesundheit & Sport,Gesellschaft & Zusammenhalt",
     0, 50000, "mind. 10 % Eigenmittel (auch Spenden, Eintritt, Teilnahmebeiträge)",
     "feste Antragsphasen, meist 01.09.–15.10., 01.01.–15.02. und 01.05.–15.06.",
     "gemeinnützige Organisationen mit Sitz in Deutschland; Laufzeit bis 24 Monate, "
     "Investitionen höchstens 10 % der Gesamtkosten",
     "bundesweit", "kostenlose Beratung über die Aktion-Mensch-Hotline",
     "Großer Hebel bei überschaubarem Eigenanteil. Vor dem Antrag anrufen – die Beratung ist gut "
     "und sagt ehrlich, ob euer Vorhaben passt.", 1),

    ("Fonds Soziokultur – Allgemeine Projektförderung", "Fonds Soziokultur e. V. (gefördert durch BKM)",
     "https://www.fonds-soziokultur.de/projektfoerderung/",
     "Partizipative soziokulturelle Projekte mit Modellcharakter: gesellschaftliche Themen mit "
     "kulturellen Mitteln bearbeiten und Teilhabe vor Ort stärken.",
     "Kultur & Begegnung,Gesellschaft & Zusammenhalt,Bildung & Jugend",
     5000, 30000, "mind. 20 % (Förderung max. 80 % der Gesamtkosten)",
     "zweimal jährlich: 02.04.–02.05. und 02.10.–02.11.",
     "freie Träger, Vereine und Initiativen; Projektdauer max. 2 Jahre, ein Antrag pro Runde",
     "bundesweit", "",
     "Inhaltlich der passgenaueste Geldgeber für Soziokultur. Nach Fristablauf wird nichts mehr "
     "angenommen – Termin im Kalender eintragen.", 1),

    ("Partnerschaft für Demokratie Waren – Aktions- und Jugendfonds",
     "Bundesprogramm „Demokratie leben!“, Stadt Waren (Müritz), Fachstelle beim CJD",
     "https://www.pfd-waren.de/",
     "Aktionsfonds für kurzfristige Kleinprojekte und Tagesveranstaltungen, dazu Initiativ- und "
     "Jugendfonds: Demokratiefeste, Workshops, Ausstellungen, Begegnungsformate.",
     "Gesellschaft & Zusammenhalt,Bildung & Jugend,Ehrenamt & Engagement",
     0, 0, "bei der Koordinierungsstelle erfragen",
     "laufend nach vorheriger Beratung mit der Koordinierungsstelle",
     "Vereine, Initiativen und engagierte Menschen aus Waren (Müritz)",
     "Waren (Müritz)", "Koordinierungsstelle: demokratie.leben@esv-waren.de; Stadt: 03991 177334",
     "Wichtig: Für Waren ist diese PfD zuständig – NICHT die des Landkreises. Für kleine Beträge "
     "der schnellste Weg überhaupt. Vorher anrufen: Die Finanzierung der PfD Waren war zuletzt "
     "nicht dauerhaft gesichert.", 0),

    ("Kinoinvestitionsprogramm „Film ab!“", "MV Filmförderung GmbH",
     "https://www.mv-filmfoerderung.de/filmfoerderung/weitere-unterstuetzung/kinoinvestitionsprogramm/",
     "Modernisierung von Kinos: Projektions- und Tontechnik, Barrierefreiheit, klimaneutrale "
     "Ausstattung. Ausdrücklich auch für ehrenamtlich betriebene Kinos und für Betreiber ohne "
     "eigene Spielstätte – als Abspielstätte zählt jeder Ort mit öffentlichen Vorführungen, auch "
     "temporär oder saisonal.",
     "Kultur & Begegnung,Bau & Ausstattung",
     0, 60000, "nur 10 % für nicht-gewerbliche Kinos (90 % Zuschuss!), 20 % für gewerbliche",
     "ganzjährig; vorherige Beratung erforderlich",
     "Kinobetriebe in MV; ein Kriterium genügt, u. a. Gemeinde unter 50.000 Einwohnern – "
     "Waren erfüllt das. Fortführung des Betriebs für 5 Jahre muss plausibel sein",
     "Mecklenburg-Vorpommern", "",
     "Die mit Abstand beste Konditionierung im ganzen Feld: 90 % Förderung für ein "
     "nicht-gewerbliches Kino, bis 60.000 €, das ganze Jahr über. Wenn im Kino etwas ansteht – "
     "Technik, Bestuhlung, Barrierefreiheit – dann hier.", 1),

    ("Kinopreis Mecklenburg-Vorpommern", "MV Filmförderung GmbH",
     "https://www.mv-filmfoerderung.de/",
     "Auszeichnung engagierter Kinos in sechs Kategorien: bestes Jahresprogramm, kulturelle "
     "Vielfalt, Nachhaltigkeit, Innovation, ehrenamtliches Engagement und lokale Kinokultur.",
     "Kultur & Begegnung,Ehrenamt & Engagement",
     0, 100000, "kein Eigenanteil – es ist ein Preis",
     "jährliche Einreichung, zuletzt bis 31. Juli",
     "ausdrücklich auch nicht-gewerbliche Spielstätten in MV",
     "Mecklenburg-Vorpommern", "",
     "Es gibt eine eigene Kategorie „ehrenamtliches Engagement“ – dafür seid ihr wie gemacht. "
     "Ihr bewerbt euch mit dem Programm, das ihr ohnehin macht. Termin jedes Jahr im Juli merken.", 1),

    ("MV Filmförderung – Abspiel- und Festivalförderung", "MV Filmförderung GmbH",
     "https://www.mv-filmfoerderung.de/",
     "Förderung von Filmvorführungen, Filmreihen, Festivals und filmkultureller Vermittlung.",
     "Kultur & Begegnung",
     0, 5000, "nicht beziffert",
     "Entscheidungen bis 5.000 € mehrmals im Jahr durch die Geschäftsführung; darüber Gremien­sitzungen",
     "Veranstalter und Spielstättenbetreiber in MV", "Mecklenburg-Vorpommern", "",
     "Für eine Filmreihe oder ein kleines Festival der passende Topf – und Entscheidungen unter "
     "5.000 € gehen ohne Gremiumssitzung, also vergleichsweise schnell.", 0),

    ("Kinoprogrammpreise des Bundes", "Der Beauftragte der Bundesregierung für Kultur und Medien (BKM)",
     "https://kulturstaatsminister.de/film-und-medien/kulturelle-filmfoerderung/preise/kinoprogrammpreis",
     "Jährliche Prämie für Kinos mit kulturell herausragendem Jahresfilmprogramm; Sonderpreise für "
     "Kinder- und Jugendfilm-, Dokumentar- und Kurzfilmprogramme.",
     "Kultur & Begegnung",
     2500, 10000, "kein Eigenanteil – es ist eine Prämie, kein Zuschuss",
     "jährliche Bewerbungsrunde, Bewerbungsschluss meist Ende Juni",
     "kleinere und mittlere Programmkinos – auch ein Vereinskino mit kuratiertem Programm",
     "bundesweit", "",
     "Für ein Vereinskino der realistischste Weg zu Geld ohne Kofinanzierung: Ihr bewerbt euch mit "
     "dem Programm, das ihr ohnehin macht. Das Zukunftsprogramm Kino (Investitionen) ist dagegen "
     "wegen erschöpfter Mittel geschlossen.", 0),

    # ── Weitere bundesweite Programme ─────────────────────────────────────
    ("Aktion Mensch – Junge Menschen stärken", "Aktion Mensch e. V.",
     "https://www.aktion-mensch.de/foerderung/foerderangebote/junge-menschen",
     "Inklusive, partizipative Freizeitprojekte, die Teilhabe und Selbstbewusstsein junger Menschen "
     "mit Teilhabeeinschränkungen stärken.",
     "Bildung & Jugend,Gesellschaft & Zusammenhalt",
     0, 300000, "mind. 10 % Eigenmittel",
     "feste Antragsphasen wie bei den anderen Aktion-Mensch-Programmen",
     "gemeinnützige Träger; Zielgruppe 12–24 Jahre, Laufzeit bis 48 Monate",
     "bundesweit", "", "Für mehrjährige Jugendarbeit mit fester Personalstelle.", 0),

    ("Aktion Mensch – Pauschalförderung Ferien und Bildung", "Aktion Mensch e. V.",
     "https://www.aktion-mensch.de/foerderung/foerderangebote/pauschalfoerderung",
     "Feste Tagespauschalen für Bildungsveranstaltungen (Seminare, Workshops) und für "
     "Begleitpersonen bei inklusiven Ferienfahrten – ohne aufwendige Einzelabrechnung.",
     "Bildung & Jugend,Gesellschaft & Zusammenhalt",
     0, 0, "bei Ferienreisen keine Eigenmittel nötig",
     "laufend; Entscheidungen sechsmal im Jahr",
     "gemeinnützige Träger", "bundesweit", "",
     "Angenehm, weil pauschal abgerechnet wird: kein Belegberg, sondern Tagessätze je "
     "Teilnehmer:in bzw. Begleitperson.", 0),

    ("Fonds Soziokultur – U25 Junge Kulturinitiativen", "Fonds Soziokultur e. V.",
     "https://www.fonds-soziokultur.de/u25/",
     "Experimentelle Kulturprojekte junger Menschen, die lokale Communities einbinden.",
     "Kultur & Begegnung,Bildung & Jugend",
     0, 4000, "ca. 10 % (bis 90 % förderfähig, Gesamtbudget max. 8.000 €)",
     "zweimal jährlich, gleiche Termine wie die allgemeine Projektförderung",
     "antragstellende Person zwischen 18 und 25 Jahren – kann sich rechtlich durch euren Verein "
     "vertreten lassen", "bundesweit", "",
     "Guter Weg, jungen Leuten im Verein ein eigenes Projekt zu ermöglichen: Sie stellen den "
     "Antrag, ihr seid nur der rechtliche Träger.", 0),

    ("Kulturstiftung des Bundes – Allgemeine Projektförderung", "Kulturstiftung des Bundes",
     "https://www.kulturstiftung-des-bundes.de/de/foerder_check_antrag/aktuelle_antragsmoeglichkeiten.html",
     "Innovative Kulturprojekte mit internationaler Ausrichtung in allen Sparten; Erprobung neuer "
     "Arbeitsweisen und Öffnung für neues Publikum.",
     "Kultur & Begegnung,Internationales & Migration",
     50000, 0, "20 % Kofinanzierung",
     "zweimal jährlich: 31. Januar und 31. Juli, jeweils 12:00 Uhr",
     "Kultureinrichtungen, Vereine und organisierte Gruppen – keine Einzelkünstler:innen; "
     "Mindestfördersumme 50.000 €",
     "bundesweit", "",
     "Erst ab 50.000 € Fördersumme – also nur für ein wirklich großes Vorhaben. Die Uhrzeit der "
     "Frist ist ernst gemeint.", 0),

    ("Kulturstiftung des Bundes – LOKAL. Programm für Kultur und Engagement",
     "Kulturstiftung des Bundes mit bpb und European Cultural Foundation",
     "https://www.kulturstiftung-des-bundes.de/de/projekte/lokal/faq_lokal.html",
     "Vierjähriger Aufbau langfristiger lokaler Netzwerke: partizipativ-künstlerische Projekte "
     "gemeinsam mit Partnern außerhalb des Kultursektors – Unternehmen, Sportvereinen, Pflegeheimen.",
     "Kultur & Begegnung,Gesellschaft & Zusammenhalt,Stadt- & Regionalentwicklung",
     200000, 240000, "mind. 10 %, davon unbare Eigenleistungen möglich",
     "⚠️ derzeit keine offene Runde – Seite beobachten, eine neue Ausschreibung ist möglich",
     "Kulturvereine und soziokulturelle Zentren mit regelmäßigem Angebot in Orten unter "
     "100.000 Einwohnenden; Rechtsform e. V., Stiftung oder GmbH",
     "bundesweit", "",
     "Inhaltlich wie für euch gemacht: kleine Stadt, Kulturverein, Netzwerk vor Ort – 240.000 € "
     "über vier Jahre bei nur 10 % Eigenleistung. Lohnt sich, die Seite regelmäßig zu prüfen.", 0),

    ("Deutsch-Französischer Bürgerfonds", "Deutsch-Französischer Bürgerfonds",
     "https://www.buergerfonds.eu/",
     "Deutsch-französische Begegnungs-, Kultur- und Bildungsprojekte aller Formate: "
     "Städtepartnerschaften, Jugendaustausch, gemeinsame Veranstaltungen.",
     "Internationales & Migration,Kultur & Begegnung,Bildung & Jugend",
     0, 50000, "mind. 20 % (Förderung bis 80 %)",
     "laufend, keine Stichtage – Antrag mindestens 6 Wochen vor Projektbeginn",
     "Vereine, Kommunen, Bildungseinrichtungen, auch informelle Gruppen ab 3 Personen",
     "bundesweit", "",
     "Angenehm: 75 % der Fördersumme werden schon vor Projektstart ausgezahlt – ihr müsst nicht "
     "in Vorleistung gehen.", 0),

    ("Kultur macht stark. Bündnisse für Bildung", "BMBFSFJ über 27 Programmpartner",
     "https://www.buendnisse-fuer-bildung.de",
     "Außerschulische kulturelle Bildungsangebote lokaler Bündnisse für bildungsbenachteiligte "
     "Kinder und Jugendliche: Theater, Musik, Film, Medien, Zirkus, Lesen.",
     "Bildung & Jugend,Kultur & Begegnung",
     0, 0, "in der Regel Vollfinanzierung ohne Eigenanteil",
     "laufend über die jeweiligen Programmpartner; Förderphase bis Ende 2027",
     "lokale Bündnisse aus mindestens 3 Partnern; Zielgruppe 3–18 Jahre",
     "bundesweit", "",
     "Wichtig: Der Antrag geht nicht an den Bund, sondern an den passenden Programmpartner – "
     "erst auf der Webseite den richtigen heraussuchen. Dafür meist ohne Eigenanteil.", 0),

    ("Stiftung Deutsches Hilfswerk (Deutsche Fernsehlotterie)", "Stiftung Deutsches Hilfswerk",
     "https://www.fernsehlotterie.de/Stiftung/informationen-foerdermittelbewerbung",
     "Soziale Projekte und Einrichtungen mit Modellcharakter – auch Bau, Sanierung und "
     "Erstausstattung; Themenfelder Quartier, Kinder/Jugend/Familie, Senior:innen.",
     "Gesellschaft & Zusammenhalt,Bau & Ausstattung",
     0, 300000, "i. d. R. 20 % Eigenmittel; bei Bau/Sanierung höherer Eigenanteil",
     "zwei Vergabesitzungen im Jahr – Bewerbungsphase auf der Webseite prüfen",
     "gemeinnützige Träger, Vereine, Verbände, Stiftungen",
     "bundesweit", "",
     "Die richtige Adresse, wenn es um Bauliches oder größere Anschaffungen geht. Lange "
     "Vorlaufzeit einplanen: von der Bewerbung bis zum Projektstart vergehen Monate.", 0),

    ("Stiftung Bildung – Förderfonds", "Stiftung Bildung",
     "https://www.stiftungbildung.org/foerderfonds/",
     "Projekte, bei denen Kinder und Jugendliche von Anfang an aktiv beteiligt sind: Kunst und "
     "Kultur, Medienkompetenz, Demokratie, Klimaschutz, Vielfalt und Inklusion.",
     "Bildung & Jugend,Gesellschaft & Zusammenhalt",
     0, 5000, "nicht ausgewiesen", "laufend",
     "⚠️ nur Kita- und Schulfördervereine sind antragsberechtigt",
     "bundesweit", "",
     "Für euch nur über einen befreundeten Kita- oder Schulförderverein als Antragsteller "
     "nutzbar. Nicht gefördert: reine Materialbeschaffung und Honorare.", 0),

    ("BULEplus – Bundesprogramm Ländliche Entwicklung", "BMLEH / Bundesanstalt für Landwirtschaft und Ernährung",
     "https://www.land-zukunft.de/foerderung/",
     "Modellvorhaben in ländlichen Räumen: Daseinsvorsorge, Begegnungsorte, Ehrenamt, "
     "Regionalversorgung, Kultur im Dorf.",
     "Stadt- & Regionalentwicklung,Gesellschaft & Zusammenhalt,Ehrenamt & Engagement",
     0, 0, "je Bekanntmachung unterschiedlich",
     "einzelne themenbezogene Förderaufrufe mit oft knappen Fristen",
     "je Aufruf unterschiedlich – häufig Kommunen, teils auch Vereine",
     "ländliche Räume bundesweit", "buleplus@ble.de, 0228 6845-2290",
     "Newsletter abonnieren: Die Aufrufe kommen unregelmäßig und laufen schnell wieder aus.", 0),

    ("Deutsche Postcode Lotterie – Projektförderung", "Deutsche Postcode Lotterie",
     "https://www.postcode-lotterie.de/guter-zweck/projektfoerderung/foerderung-beantragen",
     "Gemeinnützige Projekte aus Natur- und Umweltschutz sowie gesellschaftlichem Zusammenhalt.",
     "Umwelt & Klima,Gesellschaft & Zusammenhalt",
     0, 100000, "nicht ausgewiesen",
     "⚠️ pausiert – derzeit keine Anträge; eine neue Förderrunde ist ab 2027 angekündigt",
     "gemeinnützige Organisationen; Erstantragsteller bis 30.000 €",
     "bundesweit", "",
     "Jetzt nichts einreichen, aber für 2027 vormerken – der Antrag ist vergleichsweise schlank.", 0),

    ("Deutsche Bundesstiftung Umwelt (DBU)", "Bundesstiftung",
     "https://www.dbu.de",
     "Modellhafte, innovative Umwelt- und Naturschutzprojekte sowie Umweltbildung; kleinere "
     "Vorhaben im vereinfachten Verfahren.",
     "Umwelt & Klima,Bildung & Jugend",
     0, 0, "abhängig vom Vorhaben", "laufend",
     "Vereine, Verbände, kleine Unternehmen", "bundesweit", "",
     "Der Modellcharakter entscheidet: Was ist neu daran, und können andere es nachmachen?", 0),

    # ── Mecklenburg-Vorpommern und die Region ─────────────────────────────
    ("Kulturelle Projektförderung des Landes MV", "Kulturministerium MV über das Landesförderinstitut (LFI)",
     "https://www.lfi-mv.de/foerderfinder/kulturelle-projektfoerderung/",
     "Kulturprojekte nach dem Drei-Säulen-Modell (lokale und überregionale Grundversorgung, "
     "überregionale Projekte, innovative Vorhaben). Erfreulich: Auch laufende Basisausgaben wie "
     "Personal, Honorare, Miete und Nebenkosten sind förderfähig.",
     "Kultur & Begegnung,Gesellschaft & Zusammenhalt",
     0, 0, "Eigenmittel erforderlich",
     "1. Oktober für Projekte des Folgejahres, Antrag beim LFI MV",
     "Vereine und andere Träger mit Kulturprojekten in MV; Förderzeitraum bis 24 Monate, "
     "Basisförderung bis 4 Jahre",
     "Mecklenburg-Vorpommern", "Landesförderinstitut MV", 
     "Einer der wenigen Töpfe, die auch Miete und Personal tragen – nicht nur Projektkosten. "
     "Der 1. Oktober ist die entscheidende Jahresfrist.", 1),

    ("Bürgerfonds Kultur MV", "Kulturministerium MV über das Landesförderinstitut (LFI)",
     "https://www.lfi-mv.de/foerderfinder/buergerfonds-kultur/",
     "Gemeinwohlorientierte Kulturmaßnahmen, die durch andere Programme nicht abgedeckt sind: "
     "lokale Kulturprojekte, Weiterbildung im Kulturbereich, Stipendien. Auch als Kofinanzierung "
     "für ein LEADER-Vorhaben einsetzbar.",
     "Kultur & Begegnung,Gesellschaft & Zusammenhalt",
     1000, 15000, "nicht beziffert; Antragstellende ohne Gewinnerzielungsabsicht",
     "jährliches Antragsfenster 1. Januar bis 31. März",
     "juristische und natürliche Personen ohne kommerzielle Interessen",
     "Mecklenburg-Vorpommern", "Landesförderinstitut MV",
     "Guter Lückenfüller, wenn kein anderes Programm passt – und ausdrücklich als Kofinanzierung "
     "für LEADER gedacht, wo ihr ja einen Eigenanteil braucht.", 0),

    ("Kulturförderrichtlinie Landkreis Mecklenburgische Seenplatte", "Landkreis Mecklenburgische Seenplatte",
     "https://www.lk-mecklenburgische-seenplatte.de/Landkreis/Wirtschaft-Tourismus-Kultur/Kultur/Kulturf%C3%B6rderung/",
     "Zeitlich begrenzte künstlerische und kulturelle Projekte: bildende und darstellende Kunst, "
     "Film und Medien, Literatur, Musik, Soziokultur, Heimatpflege. Soziokultur ausdrücklich "
     "einschließlich Projekten zur Stärkung des bürgerschaftlichen Engagements.",
     "Kultur & Begegnung,Gesellschaft & Zusammenhalt",
     0, 0, "bis zu 75 % Förderung; Eigenanteil auch als unbare Arbeitsleistung möglich",
     "15. Dezember für Maßnahmen des Folgejahres – verspätete Anträge werden aus formellen "
     "Gründen abgelehnt",
     "juristische und natürliche Personen mit Bezug zum Landkreis",
     "Landkreis Mecklenburgische Seenplatte", "",
     "Bis zu 75 % und der Eigenanteil darf eure eigene Arbeitszeit sein. Nicht gefördert werden "
     "Bauinvestitionen, Speisen und Getränke, Honorare über 3.000 € sowie Dorf- und "
     "Traditionsfeste. Die Frist am 15.12. ist hart.", 1),

    ("Jugendarbeit – Projektförderung Landkreis MSE", "Landkreis Mecklenburgische Seenplatte, Jugendamt",
     "https://www.lk-mecklenburgische-seenplatte.de/Aktuelles/Formulare/Jugend-Familie/",
     "Sozialpädagogische Jahres- und Einzelprojekte: Seminare, Kurse, Musik- und "
     "Theaterveranstaltungen, Freizeitangebote mit Bildungsinhalt, Gruppenleiterschulungen.",
     "Bildung & Jugend,Gesellschaft & Zusammenhalt",
     0, 3000, "Einzelprojekte mind. 20 %, Jahresprojekte mind. 50 %; ehrenamtliche Arbeit "
     "zum Mindestlohn anrechenbar",
     "Jahresprojekte: 1. Juli des Vorjahres. Einzelprojekte quartalsweise: 1. November, "
     "28. Februar, 31. Mai, 31. August",
     "Träger der Jugendhilfe und Vereine; Zielgruppe 6 bis 27 Jahre, in der Regel mindestens "
     "7 Teilnehmende. Vereinbarung mit dem Jugendamt nach §§ 8a/72a SGB VIII nötig",
     "Landkreis Mecklenburgische Seenplatte", "Jugendamt des Landkreises", 
     "Achtung: Vorhaben mit überwiegend kulturellem Charakter sind hier ausgeschlossen – dafür "
     "ist die Kulturförderrichtlinie da.", 0),

    ("Kulturförderung der Stadt Waren (Müritz)", "Stadt Waren (Müritz)",
     "https://www.waren-mueritz.de/de/buergerservice-verwaltung/ortsrecht_satzungen/",
     "Zeitlich begrenzte künstlerische und kulturelle Vorhaben: bildende und darstellende Kunst, "
     "Film und Medien, Heimatpflege, Literatur, Musik, kulturelle Bildung und Soziokultur.",
     "Kultur & Begegnung",
     0, 0, "mind. 20 % – auch als unbare Arbeits- oder Sachleistung (mind. Mindestlohn)",
     "31. Januar für Maßnahmen des laufenden Jahres; später noch möglich, solange Mittel da sind",
     "Träger mit Sitz in Waren; das Projekt soll in der Stadt stattfinden",
     "Waren (Müritz)", "kultur@waren-mueritz.de",
     "Der kürzeste Weg, und ihr kennt die Leute. Nicht gefördert: Benefizveranstaltungen sowie "
     "kommerzielle, religiöse oder parteipolitische Vorhaben. Vor dem 31.01. beim Kulturamt nach "
     "der aktuellen Fassung der Richtlinie fragen.", 1),

    ("Vereinsförderung der Stadt Waren (Müritz)", "Stadt Waren (Müritz)",
     "https://www.waren-mueritz.de/de/buergerservice-verwaltung/ortsrecht_satzungen/",
     "Maßnahmen, die soziale Benachteiligungen abbauen und der Warener Bevölkerung zugutekommen: "
     "Seminare, Fachvorträge, Veranstaltungen, Ausflüge. Für Begegnungsstätten und "
     "Kommunikationszentren auch Sach- und Betriebskosten wie Miete, Nebenkosten und Anschaffungen.",
     "Gesellschaft & Zusammenhalt,Ehrenamt & Engagement,Bau & Ausstattung",
     0, 0, "Eigenbeteiligung in Sach- oder Geldleistungen; Drittmittel sind vorrangig auszuschöpfen",
     "31. Januar des laufenden Jahres; später nur in absoluten Ausnahmefällen",
     "Vereine, Gruppen, Verbände und Initiativen mit sozialer Arbeit in Waren",
     "Waren (Müritz)", "",
     "Bemerkenswert: Für Begegnungsstätten sind sogar laufende Betriebskosten förderfähig – das "
     "ist selten. Städtische Mittel kommen aber nur nachrangig, andere Töpfe zuerst.", 0),

    ("Zuschuss Bürgersaal Waren", "Stadt Waren (Müritz)",
     "https://www.waren-mueritz.de/de/buergerservice-verwaltung/1.15-formulare/",
     "Zuschuss zu den Saalkosten für kulturelle Veranstaltungen im Bürgersaal, die dem Gemeinwohl "
     "dienen. Dazu die kostenfreie Nutzung städtischer Verkaufshütten und Flächen bei erheblichem "
     "öffentlichem Interesse.",
     "Kultur & Begegnung",
     0, 0, "20 % der Gesamtkosten",
     "31. Januar für Maßnahmen des laufenden Jahres",
     "Vereine, Verbände, Kirchen und gemeinnützige Gesellschaften mit Bezug zu Waren",
     "Waren (Müritz)", "", 
     "Praktisch für größere Veranstaltungen – und die kostenfreien Verkaufshütten sind bei "
     "Festen bares Geld wert.", 0),

    ("Richard-Wossidlo-Kulturpreis", "Stadt Waren (Müritz)",
     "https://www.waren-mueritz.de/de/buergerservice-verwaltung/ortsrecht_satzungen/",
     "Jährlicher Anerkennungs- und Förderpreis für kulturell-schöpferische und kulturfördernde "
     "Leistungen; zusätzlich zum Preisgeld ein Kunstwerk im Wert von 250 €.",
     "Kultur & Begegnung",
     250, 500, "kein Eigenanteil – es ist ein Preis",
     "Vorschläge bis 15. Oktober; Verleihung beim Neujahrsempfang",
     "Personen, Gruppen und Ensembles aus Waren; vorschlagsberechtigt sind Bürger, Vereine "
     "und Institutionen",
     "Waren (Müritz)", "",
     "Ihr könnt auch jemand anderen vorschlagen – und selbst vorgeschlagen werden. Gute "
     "Gelegenheit, verdiente Ehrenamtliche sichtbar zu machen.", 0),

    ("LEADER – LAG Mecklenburgische Seenplatte-Müritz", "EU (ELER) über die Lokale Aktionsgruppe",
     "https://leader-mse.de/Mecklenburgische-Seenplatte-M%C3%BCritz/",
     "Projekte für Lebensqualität im ländlichen Raum, Erhalt von Umwelt und Kultur und "
     "demokratische Beteiligung. Für investive Vorhaben – Räume, Technik, Barrierefreiheit – "
     "besonders interessant.",
     "Stadt- & Regionalentwicklung,Kultur & Begegnung,Bau & Ausstattung",
     0, 312500, "Fördersatz bis zu 100 % der zuwendungsfähigen Ausgaben (bei produktiven "
     "Investitionen max. 65 %)",
     "Ideenskizzen jährlich bis 30. Juni für die Prioritätenliste der Folgejahre; die "
     "Förderperiode läuft bis Ende 2027",
     "Kommunen, Vereine und Privatpersonen im LAG-Gebiet (Ämter Seenlandschaft Waren, Malchow, "
     "Röbel-Müritz, Mecklenburgische Kleinseenplatte); Unternehmen sind ausgeschlossen",
     "Mecklenburgische Seenplatte-Müritz", "Geschäftsstelle: Zum Amtsbrink 2, Waren; "
     "Regionalmanagement 0395 57087 2570",
     "Die größte Summe im ganzen Feld – bis zu 312.500 € und im Idealfall ohne Eigenanteil. "
     "Die Geschäftsstelle sitzt in Waren. Rechtzeitig vor dem 30. Juni beraten lassen: Ohne "
     "Ideenskizze in der Prioritätenliste läuft nichts.", 1),

    ("Gutes tun in MV", "Ehrenamtsstiftung Mecklenburg-Vorpommern",
     "https://www.ehrenamtsstiftung-mv.de/gutes-tun-in-mv/foerdern/",
     "Ehrenamtlich getragene Vorhaben, die Gemeinschaft und Zusammenhalt in MV stärken – "
     "Bildung, Kultur, Soziales, Umwelt, Demokratieengagement.",
     "Ehrenamt & Engagement,Gesellschaft & Zusammenhalt,Bau & Ausstattung",
     0, 3000, "kein fester Eigenanteil ausgewiesen",
     "laufend ohne Stichtage; Antrag mindestens 12 Wochen vor Beginn über das EAS-MV-Portal",
     "gemeinnützige Vereine mit Sitz in MV, ausdrücklich auch Klein- und Kleinstvereine; "
     "im Regelfall bis 1.000 €, in Ausnahmen bis 3.000 €",
     "Mecklenburg-Vorpommern", "EAS-MV-Portal",
     "Seit 2026 ohne detaillierten Verwendungsnachweis – nur noch eine einfache Erklärung. "
     "Damit einer der unbürokratischsten Töpfe überhaupt. Gelegentlich gibt es Antragspausen, "
     "vorher kurz auf der Seite schauen.", 1),

    ("Junges Ehrenamt", "Ehrenamtsstiftung Mecklenburg-Vorpommern",
     "https://www.ehrenamtsstiftung-mv.de/gutes-tun-in-mv/foerdern/junges-ehrenamt/",
     "Projektideen junger Engagierter, die im Verein umgesetzt werden. Der Antrag besteht nur aus "
     "einer kurzen Projektbeschreibung und den Vereinsdaten.",
     "Ehrenamt & Engagement,Bildung & Jugend",
     0, 1000, "kein Eigenanteil; Gesamtkosten dürfen 1.000 € nicht übersteigen",
     "laufend; Antrag spätestens 12 Wochen vor der Umsetzung",
     "Engagierte bis 27 Jahre in einem eingetragenen Verein in MV; der Vorstand muss die Idee "
     "unterstützen, Freistellungsbescheid und Satzung digital vorlegen",
     "Mecklenburg-Vorpommern", "EAS-MV-Portal",
     "Der einfachste Antrag im ganzen Verzeichnis. Ideal, um jungen Leuten im Verein ein eigenes "
     "Projekt zu ermöglichen – ihr seid nur der Träger.", 0),

    ("Projektförderung Soziokultur MV", "Landesverband Soziokultur MV e. V. im Auftrag des Kulturministeriums",
     "https://lv-soziokultur-mv.de/service/projektfoerderung/",
     "Soziokulturelle Projekte in MV. Seit Oktober 2025 die zentrale Anlaufstelle – Anträge "
     "laufen über dieses Portal statt über das LFI. Daneben eine Mikroförderlinie bis 1.000 €.",
     "Kultur & Begegnung,Gesellschaft & Zusammenhalt",
     0, 5000, "nicht ausgewiesen – bei der Fachstelle erfragen",
     "⚠️ Antragsfenster meist im Herbst für das Folgejahr; die Landesmittel sind erfahrungsgemäß "
     "früh ausgeschöpft. Neue Ausschreibungen erscheinen im letzten Quartal",
     "soziokulturelle Zentren, Kulturvereine und Initiativen in MV",
     "Mecklenburg-Vorpommern", "Fachstelle Soziokultur MV, Greifswald",
     "Kurze Wege und überschaubarer Antrag – aber sofort beantragen, wenn das Fenster aufgeht. "
     "Wer wartet, findet einen leeren Topf.", 0),

    ("Ostdeutsche Sparkassenstiftung – Projektförderung", "Ostdeutsche Sparkassenstiftung, örtlich über die Müritz-Sparkasse",
     "https://ostdeutsche-sparkassenstiftung.de/foerderung/",
     "Reine Kulturstiftung: Museen, Denkmalpflege, bildende Kunst, Musik, Literatur und "
     "darstellende Kunst. Erwartet werden herausragende Qualität und überregionale Bedeutung.",
     "Kultur & Begegnung",
     0, 0, "Eigenmittel zwingend nachzuweisen; weitere Quellen sind auszuschöpfen",
     "10. Januar (Frühjahrssitzung) und 10. Juli (Herbstsitzung), Einreichung über die "
     "Müritz-Sparkasse",
     "gemeinnützige Träger mit Sitz in MV; Gemeinnützigkeitsbescheinigung, Kosten- und "
     "Finanzierungsplan sowie bei Aufträgen drei Vergleichsangebote erforderlich",
     "Mecklenburg-Vorpommern", "über die Müritz-Sparkasse (Portal CLARA)",
     "Langer Vorlauf: 12 bis 18 Monate vor Projektbeginn einreichen. Dafür ordentliche Summen "
     "für Vorhaben mit Ausstrahlung über Waren hinaus.", 0),

    ("Müritz-Sparkasse – Vereinsförderung und Sponsoring", "Müritz-Sparkasse",
     "https://www.mueritz-sparkasse.de",
     "Aus dem PS-Lotterie-Sparen fließt ein fester Anteil je Los an Vereine und Projekte der "
     "Region. Dazu regelmäßige Spendenaktionen und klassisches Sponsoring.",
     "Kultur & Begegnung,Gesellschaft & Zusammenhalt,Gesundheit & Sport",
     0, 0, "keiner", "Bewerbungszeiträume werden ausgeschrieben – direkt nachfragen",
     "gemeinnützige Vereine mit Konto bei der Müritz-Sparkasse und Wirken im Geschäftsgebiet",
     "Müritzregion", "Müritz-Sparkasse, 03991 636-0",
     "Formal keine Förderung, praktisch oft die schnellste Zusage. Ein formloses Anschreiben "
     "genügt meist – Gegenleistung wie Logo auf dem Plakat gleich anbieten. Wichtig: Die Mittel "
     "dürfen nicht für Personal- oder Verwaltungskosten verwendet werden.", 0),

    ("Politische Bildung – Projektförderung", "Landeszentrale für politische Bildung MV",
     "https://www.lpb-mv.de/foerderung/projekte-zur-politischen-bildung/",
     "Veranstaltungen und Materialien der außerschulischen politischen Bildung: Grundgesetz und "
     "demokratische Ordnung, Auseinandersetzung mit Extremismus, Landeskunde, Geschichte des "
     "20. Jahrhunderts, europäische Einigung, Ostseeraum.",
     "Bildung & Jugend,Gesellschaft & Zusammenhalt",
     0, 0, "50 % Eigenbeteiligung bzw. Drittmittel",
     "keine feste Frist; Antrag vor Beginn, etwa 4 Wochen Vorlauf einplanen",
     "Vereine, Bildungsanbieter und Kommunen; mindestens 10 Teilnehmende, davon mindestens 80 % "
     "aus MV, Durchführung in MV",
     "Mecklenburg-Vorpommern", "Landeszentrale für politische Bildung MV",
     "Passt gut zu Vortrags- und Diskussionsreihen. Nicht gefördert: berufliche Weiterbildung, "
     "touristische Programme und reine Organisationstagungen.", 0),

    ("Demokratie und Toleranz gemeinsam stärken", "Landeszentrale für politische Bildung MV (ESF Plus)",
     "https://www.lpb-mv.de/foerderung/projekte-zur-staerkung-von-demokratie-und-toleranz/",
     "Projekte zur Aufklärung über antidemokratische Bestrebungen und zum Aufbau von "
     "Demokratiekompetenzen wie Konfliktfähigkeit und interkultureller Kompetenz. Dazu pauschal "
     "geförderte Mikroprojekte.",
     "Gesellschaft & Zusammenhalt,Bildung & Jugend",
     500, 0, "mind. 20 % (Förderung bis 80 %); Mikroprojekte pauschal 500 €",
     "keine festen Stichtage; Antrag vor Beginn. Programmlaufzeit bis 2027",
     "natürliche und juristische Personen; das Projekt muss in MV stattfinden",
     "Mecklenburg-Vorpommern", "Landeszentrale für politische Bildung MV",
     "Die Mikroprojekt-Pauschale von 500 € ist bemerkenswert einfach: mindestens 5 Beteiligte, "
     "je mindestens 4 Stunden – fertig.", 0),

    ("BINGO! Umweltlotterie – Projektförderung", "Norddeutsche Stiftung für Umwelt und Entwicklung (NUE)",
     "https://www.nue-stiftung.de/bingo-umweltlotterie",
     "Natur- und Umweltschutz, Umweltbildung und Entwicklungszusammenarbeit. In MV werden "
     "jährlich rund 1,5 Mio. € aus der BINGO!-Lotterie ausgeschüttet.",
     "Umwelt & Klima,Bildung & Jugend",
     0, 0, "nicht beziffert – Beratung über die Regionalstelle MV",
     "Antrag vor Beginn des Vorhabens; die Vergaberäte tagen quartalsweise",
     "gemeinnützige Vereine mit Sitz oder Wirkungskreis in MV; im Einzelfall auch nicht "
     "eingetragene Initiativen",
     "Mecklenburg-Vorpommern", "Regionalstelle MV",
     "Auch für einen Kulturverein erreichbar, sobald ein Projekt einen Umwelt- oder "
     "Nachhaltigkeitsbezug hat – etwa eine Klimafilmreihe oder ein Umweltbildungsangebot.", 0),

    ("Landesförderinstitut Mecklenburg-Vorpommern (LFI)", "Land Mecklenburg-Vorpommern",
     "https://www.lfi-mv.de/foerderfinder/",
     "Zentrale Bewilligungsstelle für Landes- und EU-Förderung in MV: Infrastruktur, Städtebau, "
     "Arbeit und Soziales (ESF+), ländliche Entwicklung (ELER).",
     "Stadt- & Regionalentwicklung,Bau & Ausstattung,Gesellschaft & Zusammenhalt",
     0, 0, "meist erforderlich", "je Richtlinie unterschiedlich",
     "Kommunen, Vereine, Unternehmen", "Mecklenburg-Vorpommern", "",
     "Kein einzelnes Programm, sondern das Dach über vielen. Der Förderfinder auf der Webseite "
     "ist der schnellste Weg zu einer passenden Richtlinie.", 0),

    ("Stiftung Mecklenburg-Vorpommern", "Landesstiftung",
     "https://www.stiftung-mv.de",
     "Kultur, Denkmalpflege, Wissenschaft und regionale Identität in MV – auch kleinere "
     "Projektsummen für Vereine.",
     "Kultur & Begegnung,Stadt- & Regionalentwicklung",
     0, 0, "üblich", "die Sitzungstermine des Stiftungsrats bestimmen den Zeitplan",
     "Vereine, Kommunen, Einrichtungen", "Mecklenburg-Vorpommern", "",
     "Rechtzeitig vor einer Sitzung einreichen, sonst wartet der Antrag ein halbes Jahr.", 0),

    ("Landesjugendplan Mecklenburg-Vorpommern", "Sozialministerium MV / Landesjugendring",
     "https://www.ljrmv.de",
     "Förderung der Jugendarbeit: Freizeiten, Jugendbildung, internationale Begegnungen, "
     "Qualifizierung Ehrenamtlicher.",
     "Bildung & Jugend,Ehrenamt & Engagement",
     0, 0, "je nach Position", "Antrag meist bis Herbst für das Folgejahr",
     "anerkannte Träger der Jugendarbeit und Jugendverbände", "Mecklenburg-Vorpommern", "",
     "Setzt in der Regel die Anerkennung als Träger der freien Jugendhilfe voraus – notfalls "
     "über einen Partnerverein beantragen.", 0),

    ("Erinnerungskultur und Kulturgutsicherung MV", "Kulturministerium MV / Landeszentrale für politische Bildung",
     "https://www.regierung-mv.de/Landesregierung/wkm/Kultur/Kulturf%C3%B6rdersonderprogramme/",
     "Zwei Sonderprogramme: Modernisierung von Ausstellungen und neue Medien in der "
     "historisch-politischen Bildung sowie Konservierung, Digitalisierung und Restaurierung von "
     "schriftlichem und audiovisuellem Kulturgut.",
     "Kultur & Begegnung,Bildung & Jugend",
     0, 0, "bei der Erinnerungskultur bis zu 50 % Kofinanzierung",
     "Termine werden nicht öffentlich ausgeschrieben – bei der Landeszentrale bzw. dem LFI erfragen",
     "Betreiber von Gedenkstätten, Anbieter politischer Bildung, Archive, Bibliotheken, Museen",
     "Mecklenburg-Vorpommern", "",
     "Interessant, wenn ihr einen eigenen Archiv-, Film- oder Fotobestand habt, der digitalisiert "
     "werden müsste.", 0),

    ("BürgerStiftung Neubrandenburg", "BürgerStiftung Neubrandenburg",
     "https://www.buergerstiftung-neubrandenburg.de/foerderung/",
     "Projekte aus Bildung, Kunst und Kultur sowie Sport mit hohem ehrenamtlichem Anteil und "
     "Modellcharakter.",
     "Kultur & Begegnung,Bildung & Jugend,Gesundheit & Sport",
     0, 1000, "nicht ausgewiesen; hoher ehrenamtlicher Anteil wird vorausgesetzt",
     "Antragsfrist 1. Januar bis 30. März, ausschließlich digital; Abrechnung bis 31. Oktober",
     "gemeinnützige Initiativen und Vereine mit Bezug zu Neubrandenburg",
     "Region Neubrandenburg", "",
     "Für Waren nur eingeschränkt: Es braucht einen erkennbaren Bezug zu Neubrandenburg.", 0),

]


def sicherstellen(conn, verein_id):
    """Sät die Programmliste einmalig je Verein. Fehler blockieren nie die Seite."""
    try:
        v = conn.execute("SELECT foerder_seed FROM vereine WHERE id=?", (verein_id,)).fetchone()
        if v and (v["foerder_seed"] or 0) >= SEED_VERSION:
            return
        for eintrag in PROGRAMME:
            da = conn.execute("SELECT 1 FROM foerdergeber WHERE verein_id=? AND name=?",
                              (verein_id, eintrag[0])).fetchone()
            if da:
                continue
            conn.execute(
                "INSERT INTO foerdergeber (verein_id, name, traeger, url, was, bereiche, summe_von, "
                "summe_bis, eigenanteil, fristen, zielgruppe, region, kontakt, notiz, favorit, "
                "quelle, erstellt_am) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (verein_id, *eintrag, "Recherche Juli 2026", now_iso()))
        conn.execute("UPDATE vereine SET foerder_seed=? WHERE id=?", (SEED_VERSION, verein_id))
        conn.commit()
    except Exception:  # noqa: BLE001 – der Bereich muss auch ohne Seed funktionieren
        logging.getLogger("foerder_seed").exception("Fördertopf-Seed fehlgeschlagen")
