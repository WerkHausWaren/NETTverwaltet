"""Leichtgewichtige Übersetzungen für die ÖFFENTLICHEN Seiten (Stufe 1: EN, UK).

Schlüssel = deutscher Originaltext (bzw. tb_* für lange Absätze der
Teilnahmebedingungen). ue("…") liefert den Text in der aktiven Sprache;
fehlt eine Übersetzung, bleibt es beim Deutschen. Sprachwahl: ?lang=en
(wird in der Session gemerkt), sonst Browser-Sprache (Accept-Language).
"""
from flask import request, session

SPRACHEN = {"de": "DE", "en": "EN", "uk": "УК"}

# Lange Absätze bekommen Kurz-Schlüssel; deutsche Fassung steht hier.
DE = {
    "tb_titel": "Teilnahmebedingungen für Veranstaltungen",
    "tb_intro": "Diese Bedingungen gelten für die Anmeldung zu Veranstaltungen von",
    "tb_1t": "1. Anmeldung",
    "tb_1": "Mit dem Absenden des Anmeldeformulars meldest du dich verbindlich an. Du erhältst eine "
            "Bestätigung mit deinem persönlichen Voucher; mit angegebener E-Mail-Adresse schicken wir dir "
            "Voucher und Storno-Link zusätzlich per E-Mail.",
    "tb_2t": "2. Teilnahmebeitrag & Bezahlung",
    "tb_2": "Ist ein Beitrag ausgeschrieben, gilt er pro Person und ist bis zum Veranstaltungsbeginn zu "
            "zahlen – die Zahlungswege stehen in der Bestätigung. Freiwillige Zusatzspenden sind genau das: freiwillig.",
    "tb_3t": "3. Warteliste",
    "tb_3": "Ist eine Veranstaltung ausgebucht, nehmen wir dich automatisch auf die Warteliste und melden "
            "uns, sobald ein Platz frei wird. Erst mit dem Nachrücken wird der Beitrag fällig.",
    "tb_4t": "4. Stornierung & Absage",
    "tb_4": "Du kannst deine Anmeldung jederzeit über den Storno-Link in deiner Bestätigung zurückziehen. "
            "Gilt für eine Veranstaltung eine besondere Stornoregel (z. B. Frist oder Gebühr), steht sie "
            "direkt auf der Anmeldeseite. Sagen wir eine Veranstaltung ab, erstatten wir bereits gezahlte "
            "Beiträge vollständig.",
    "tb_5t": "5. Fotos",
    "tb_5": "Fotos von Teilnehmenden veröffentlichen wir nur, wenn du dem bei der Anmeldung ausdrücklich "
            "zugestimmt hast.",
    "tb_6t": "6. Haftung",
    "tb_6": "Wir haften für Vorsatz und grobe Fahrlässigkeit sowie uneingeschränkt für Schäden an Leben, "
            "Körper und Gesundheit. Im Übrigen ist die Haftung des Vereins und seiner Ehrenamtlichen auf "
            "die Verletzung wesentlicher Vertragspflichten beschränkt. Die Teilnahme erfolgt auf eigene Verantwortung.",
    "tb_7t": "7. Datenschutz",
    "tb_7": "Deine Angaben verwenden wir nur zur Abwicklung der Veranstaltung. Einzelheiten stehen in unserer",
    "tb_ds": "Datenschutzerklärung",
    "tb_fragen": "Fragen? Schreib uns",
    "tb_hilfe": "– wir helfen gern.",
    "erkl_1": "Nach dem Klick ist dein Platz reserviert: Du siehst sofort eine Bestätigung mit deinem Voucher.",
    "erkl_2": "Mit E-Mail-Adresse bekommst du Voucher und Storno-Link zugeschickt.",
    "erkl_2p": "Mit E-Mail-Adresse bekommst du Voucher, Storno-Link und die Zahlungsinfos zugeschickt.",
    "erkl_3": "Ist die Veranstaltung ausgebucht, kommst du automatisch auf die Warteliste und wir melden uns, "
              "sobald ein Platz frei wird.",
}

EN = {
    "Anmeldung": "Registration",
    "‹ Zurück zum Termin": "‹ Back to the event",
    "pro Person": "per person",
    "Mit Unterstützung von": "Supported by",
    "Aktueller Anmeldestand": "Current registration status",
    "anonyme Zahlen für Partner:innen und Sponsor:innen": "anonymous figures for partners and sponsors",
    "Anmeldestand": "Registration status",
    "Angemeldet": "Registered",
    "Freie Plätze": "Free seats",
    "Warteliste": "Waiting list",
    "%s von %s Plätzen belegt": "%s of %s seats taken",
    "Stand:": "As of:",
    "Diese Übersicht zeigt nur Zahlen – keine Namen und keine Kontaktdaten.":
        "This overview shows numbers only – no names and no contact details.",
    "Zur Anmeldeseite": "To the registration page",
    "Sponsor:in": "sponsor",
    "Ausgebucht": "Fully booked",
    "– eine Anmeldung setzt dich auf die Warteliste.": "– registering puts you on the waiting list.",
    "🪑 Noch %s Plätze frei.": "🪑 %s seats left.",
    "🔥 Nur noch wenige Plätze frei!": "🔥 Only a few seats left!",
    "🪑 Es sind noch Plätze frei.": "🪑 Seats are still available.",
    "Anmeldeschluss:": "Registration deadline:",
    "↩️ Stornierung:": "↩️ Cancellation policy:",
    "⛔ Diese Veranstaltung wurde abgesagt": "⛔ This event has been cancelled",
    "Bereits Angemeldete werden informiert. Fragen an": "Everyone registered will be notified. Questions:",
    "👥 Nur für Mitglieder": "👥 Members only",
    "nur_mitglieder_text": "This event is internal – members register directly in the members app. "
                           "Interested in becoming a member? Write to us:",
    "Die Anmeldung ist geschlossen.": "Registration is closed.",
    "Bitte wende dich direkt an": "Please contact",
    "voll_warteliste": "This event is fully booked – you can join the waiting list.",
    "vorbefuellt": "✨ We pre-filled your details from your account – please double-check them.",
    "Deine Angaben": "Your details",
    "Dein Name *": "Your name *",
    "E-Mail": "Email",
    "Telefon": "Phone",
    "Straße & Nr.": "Street & no.",
    "PLZ": "Postcode",
    "Ort": "City",
    "Wie hast du von uns erfahren?": "How did you hear about us?",
    "quelle_platzhalter": "e.g. flyer, friends, Facebook …",
    "Teilnehmende": "Participants",
    "Namen der Teilnehmende (ein Name pro Zeile) *": "Participant names (one per line) *",
    "Trag hier alle Personen ein, die teilnehmen.": "List everyone who will attend.",
    "💛 Freiwillige Spende (optional)": "💛 Voluntary donation (optional)",
    "spende_text": "Would you like to support our work? Every amount helps – voluntary and in addition "
                   "to the participation fee.",
    "Ich akzeptiere die": "I accept the",
    "Teilnahmebedingungen": "terms of participation",
    "Ich bin einverstanden, dass bei der Veranstaltung Fotos gemacht werden.":
        "I agree that photos may be taken at the event.",
    "Ihr dürft mir künftig Infos zu Veranstaltungen schicken.":
        "You may send me information about future events.",
    "Ich interessiere mich für eine Mitgliedschaft.": "I am interested in becoming a member.",
    "stern_werkhaus": "⭐ I would like to become a WerkHaus-Stern and support you regularly. "
                      "You may contact me about this by e-mail or phone (revocable at any time).",
    "stern_neutral": "⭐ I would like to support the association regularly. "
                     "You may contact me about this by e-mail or phone (revocable at any time).",
    "verbindlich anmelden": "complete registration",
    "erkl_1": "After clicking, your place is reserved: you will immediately see a confirmation with your voucher.",
    "erkl_2": "With an email address you will also receive the voucher and a cancellation link.",
    "erkl_2p": "With an email address you will also receive the voucher, a cancellation link and payment details.",
    "erkl_3": "If the event is fully booked you are placed on the waiting list automatically and we will "
              "contact you as soon as a seat becomes available.",
    "Die Anmeldung ist leider geschlossen.": "Sorry, registration is closed.",
    "Bitte Name und mindestens eine teilnehmende Person angeben.":
        "Please enter your name and at least one participant.",
    # Danke-Seite
    "Danke": "Thank you",
    "📅 Weitere Termine ansehen": "📅 See more events",
    "‹ Zur Startseite": "‹ Home",
    "Anmeldung eingegangen!": "Registration received!",
    "danke_text": "Thank you for registering for",
    "Eine Bestätigung geht an": "A confirmation will be sent to",
    "warte_box": "The event is currently fully booked – you are on the waiting list and we will contact "
                 "you as soon as a seat becomes available.",
    "💳 Jetzt online bezahlen": "💳 Pay online now",
    "ueberweisung_zweck": "Paying by bank transfer? Please use this payment reference:",
    "paypal_hinweis": "Opens PayPal with the amount pre-filled – or simply pay by bank transfer "
                      "(account details are in your confirmation email).",
    "spende_danke": "💛 Includes a voluntary donation of %s € – thank you so much for your support!",
    "🎫 Voucher ansehen": "🎫 View voucher",
    "Du kannst doch nicht kommen?": "Can't make it after all?",
    "Hier stornieren": "Cancel here",
    "storno_mail_hinweis": "– the link is also in your confirmation email.",
    "Danke für dein Feedback!": "Thank you for your feedback!",
    "feedback_text": "Your feedback on",
    "feedback_text2": "helps us a lot.",
    # Storno-Seite
    "Reservierung stornieren": "Cancel reservation",
    "Reservierung storniert": "Reservation cancelled",
    "storniert_text": "Your reservation for",
    "storniert_text2": "has been cancelled. Sorry it didn't work out – maybe next time!",
    "Zu spät": "Too late",
    "zu_spaet_text": "The event has already taken place – cancellation is no longer possible.",
    "Reservierung stornieren?": "Cancel reservation?",
    "storno_frage": "Do you really want to cancel this reservation? This cannot be undone – "
                    "but you can register again at any time.",
    "✖ Ja, Reservierung stornieren": "✖ Yes, cancel reservation",
    "Fragen? Melde dich einfach bei uns": "Questions? Just get in touch",
    "am": "on",
    # Bedingungen
    "tb_titel": "Terms of participation for events",
    "tb_intro": "These terms apply to registrations for events organised by",
    "tb_1t": "1. Registration",
    "tb_1": "By submitting the registration form you register bindingly. You will receive a confirmation "
            "with your personal voucher; if you provide an email address we will also send you the voucher "
            "and a cancellation link by email.",
    "tb_2t": "2. Fee & payment",
    "tb_2": "If a fee is stated, it applies per person and is due before the event starts – payment "
            "options are listed in the confirmation. Voluntary extra donations are exactly that: voluntary.",
    "tb_3t": "3. Waiting list",
    "tb_3": "If an event is fully booked we automatically place you on the waiting list and contact you "
            "as soon as a seat becomes available. The fee is only due once you move up.",
    "tb_4t": "4. Cancellation",
    "tb_4": "You can withdraw your registration at any time via the cancellation link in your confirmation. "
            "If special cancellation rules apply to an event (e.g. deadline or fee), they are shown on the "
            "registration page. If we cancel an event, fees already paid are refunded in full.",
    "tb_5t": "5. Photos",
    "tb_5": "We only publish photos of participants if you explicitly agreed to this when registering.",
    "tb_6t": "6. Liability",
    "tb_6": "We are liable for intent and gross negligence and without limitation for injury to life, "
            "body and health. Otherwise the liability of the association and its volunteers is limited to "
            "the breach of essential contractual obligations. Participation is at your own risk.",
    "tb_7t": "7. Privacy",
    "tb_7": "We use your details only to organise the event. For details see our",
    "tb_ds": "privacy policy",
    "tb_fragen": "Questions? Write to us",
    "tb_hilfe": "– we are happy to help.",
}

UK = {
    "Anmeldung": "Реєстрація",
    "‹ Zurück zum Termin": "‹ Назад до події",
    "pro Person": "з особи",
    "Mit Unterstützung von": "За підтримки",
    "Aktueller Anmeldestand": "Поточний стан реєстрації",
    "anonyme Zahlen für Partner:innen und Sponsor:innen": "анонімні цифри для партнерів і спонсорів",
    "Anmeldestand": "Стан реєстрації",
    "Angemeldet": "Зареєстровано",
    "Freie Plätze": "Вільні місця",
    "Warteliste": "Список очікування",
    "%s von %s Plätzen belegt": "Зайнято %s із %s місць",
    "Stand:": "Станом на:",
    "Diese Übersicht zeigt nur Zahlen – keine Namen und keine Kontaktdaten.":
        "Цей огляд показує лише цифри – без імен і контактних даних.",
    "Zur Anmeldeseite": "До сторінки реєстрації",
    "Sponsor:in": "спонсор",
    "Ausgebucht": "Місць немає",
    "– eine Anmeldung setzt dich auf die Warteliste.": "– реєстрація додасть вас до списку очікування.",
    "🪑 Noch %s Plätze frei.": "🪑 Вільних місць: %s.",
    "🔥 Nur noch wenige Plätze frei!": "🔥 Залишилося лише кілька місць!",
    "🪑 Es sind noch Plätze frei.": "🪑 Ще є вільні місця.",
    "Anmeldeschluss:": "Кінець реєстрації:",
    "↩️ Stornierung:": "↩️ Умови скасування:",
    "⛔ Diese Veranstaltung wurde abgesagt": "⛔ Цю подію скасовано",
    "Bereits Angemeldete werden informiert. Fragen an": "Усіх зареєстрованих буде поінформовано. Питання:",
    "👥 Nur für Mitglieder": "👥 Лише для членів",
    "nur_mitglieder_text": "Це внутрішня подія – члени реєструються в застосунку для членів. "
                           "Цікавить членство? Напишіть нам:",
    "Die Anmeldung ist geschlossen.": "Реєстрацію закрито.",
    "Bitte wende dich direkt an": "Будь ласка, зверніться до",
    "voll_warteliste": "Усі місця зайняті – ви можете стати в список очікування.",
    "vorbefuellt": "✨ Ми заповнили ваші дані з вашого облікового запису – будь ласка, перевірте їх.",
    "Deine Angaben": "Ваші дані",
    "Dein Name *": "Ваше ім'я *",
    "E-Mail": "Ел. пошта",
    "Telefon": "Телефон",
    "Straße & Nr.": "Вулиця та будинок",
    "PLZ": "Індекс",
    "Ort": "Місто",
    "Wie hast du von uns erfahren?": "Звідки ви про нас дізналися?",
    "quelle_platzhalter": "напр. флаєр, друзі, Facebook …",
    "Teilnehmende": "Учасники",
    "Namen der Teilnehmende (ein Name pro Zeile) *": "Імена учасників (по одному в рядку) *",
    "Trag hier alle Personen ein, die teilnehmen.": "Вкажіть усіх, хто братиме участь.",
    "💛 Freiwillige Spende (optional)": "💛 Добровільна пожертва (необов'язково)",
    "spende_text": "Бажаєте додатково підтримати нашу роботу? Кожна сума допомагає – добровільно "
                   "та додатково до внеску за участь.",
    "Ich akzeptiere die": "Я приймаю",
    "Teilnahmebedingungen": "умови участі",
    "Ich bin einverstanden, dass bei der Veranstaltung Fotos gemacht werden.":
        "Я погоджуюся, що під час події можуть робити фотографії.",
    "Ihr dürft mir künftig Infos zu Veranstaltungen schicken.":
        "Ви можете надсилати мені інформацію про майбутні події.",
    "Ich interessiere mich für eine Mitgliedschaft.": "Мене цікавить членство.",
    "stern_werkhaus": "⭐ Я хочу стати WerkHaus-Stern і підтримувати регулярно. "
                      "Зі мною можна зв'язатися щодо цього електронною поштою або телефоном (відкликання можливе будь-коли).",
    "stern_neutral": "⭐ Я хочу регулярно підтримувати об'єднання. "
                     "Зі мною можна зв'язатися щодо цього електронною поштою або телефоном (відкликання можливе будь-коли).",
    "verbindlich anmelden": "зареєструватися",
    "erkl_1": "Після натискання ваше місце заброньовано: ви одразу побачите підтвердження з вашим ваучером.",
    "erkl_2": "Якщо ви вказали ел. пошту, ми надішлемо вам ваучер і посилання для скасування.",
    "erkl_2p": "Якщо ви вказали ел. пошту, ми надішлемо вам ваучер, посилання для скасування та платіжні дані.",
    "erkl_3": "Якщо місць немає, ви автоматично потрапите до списку очікування, і ми зв'яжемося з вами, "
              "щойно з'явиться місце.",
    "Die Anmeldung ist leider geschlossen.": "На жаль, реєстрацію закрито.",
    "Bitte Name und mindestens eine teilnehmende Person angeben.":
        "Будь ласка, вкажіть ім'я та щонайменше одного учасника.",
    "Danke": "Дякуємо",
    "📅 Weitere Termine ansehen": "📅 Більше подій",
    "‹ Zur Startseite": "‹ На головну",
    "Anmeldung eingegangen!": "Реєстрацію отримано!",
    "danke_text": "Дякуємо за реєстрацію на",
    "Eine Bestätigung geht an": "Підтвердження буде надіслано на",
    "warte_box": "Наразі всі місця зайняті – ви в списку очікування, і ми зв'яжемося з вами, "
                 "щойно з'явиться місце.",
    "💳 Jetzt online bezahlen": "💳 Оплатити онлайн",
    "ueberweisung_zweck": "Сплачуєте переказом? Будь ласка, вкажіть це призначення платежу:",
    "paypal_hinweis": "Відкриє PayPal із заповненою сумою – або просто зробіть банківський переказ "
                      "(реквізити в листі з підтвердженням).",
    "spende_danke": "💛 Включно з добровільною пожертвою %s € – щиро дякуємо за підтримку!",
    "🎫 Voucher ansehen": "🎫 Переглянути ваучер",
    "Du kannst doch nicht kommen?": "Не зможете прийти?",
    "Hier stornieren": "Скасувати тут",
    "storno_mail_hinweis": "– посилання також є в листі з підтвердженням.",
    "Danke für dein Feedback!": "Дякуємо за відгук!",
    "feedback_text": "Ваш відгук про",
    "feedback_text2": "дуже нам допомагає.",
    "Reservierung stornieren": "Скасувати бронювання",
    "Reservierung storniert": "Бронювання скасовано",
    "storniert_text": "Ваше бронювання на",
    "storniert_text2": "скасовано. Шкода, що не вийшло – можливо, наступного разу!",
    "Zu spät": "Запізно",
    "zu_spaet_text": "Подія вже відбулася – скасування більше неможливе.",
    "Reservierung stornieren?": "Скасувати бронювання?",
    "storno_frage": "Ви справді хочете скасувати це бронювання? Це незворотно – але ви завжди "
                    "можете зареєструватися знову.",
    "✖ Ja, Reservierung stornieren": "✖ Так, скасувати бронювання",
    "Fragen? Melde dich einfach bei uns": "Питання? Просто напишіть нам",
    "am": "",
    "tb_titel": "Умови участі в подіях",
    "tb_intro": "Ці умови діють для реєстрації на події, які організовує",
    "tb_1t": "1. Реєстрація",
    "tb_1": "Надсилаючи форму, ви реєструєтеся зобов'язально. Ви отримаєте підтвердження з особистим "
            "ваучером; якщо вказано ел. пошту, ми додатково надішлемо ваучер і посилання для скасування.",
    "tb_2t": "2. Внесок та оплата",
    "tb_2": "Якщо вказано внесок, він діє з особи та має бути сплачений до початку події – способи "
            "оплати вказані в підтвердженні. Додаткові пожертви є добровільними.",
    "tb_3t": "3. Список очікування",
    "tb_3": "Якщо місць немає, ми автоматично додамо вас до списку очікування та повідомимо, щойно "
            "з'явиться місце. Внесок сплачується лише після отримання місця.",
    "tb_4t": "4. Скасування",
    "tb_4": "Ви можете будь-коли відкликати реєстрацію за посиланням у підтвердженні. Якщо для події "
            "діють особливі правила скасування (термін або збір), вони вказані на сторінці реєстрації. "
            "Якщо ми скасовуємо подію, сплачені внески повертаються повністю.",
    "tb_5t": "5. Фотографії",
    "tb_5": "Ми публікуємо фото учасників лише за вашої явної згоди під час реєстрації.",
    "tb_6t": "6. Відповідальність",
    "tb_6": "Ми відповідаємо за умисел і грубу недбалість, а також без обмежень за шкоду життю та "
            "здоров'ю. В іншому відповідальність об'єднання та його волонтерів обмежена порушенням "
            "істотних договірних обов'язків. Участь на власний ризик.",
    "tb_7t": "7. Захист даних",
    "tb_7": "Ваші дані ми використовуємо лише для організації події. Подробиці в нашій",
    "tb_ds": "політиці конфіденційності",
    "tb_fragen": "Питання? Напишіть нам",
    "tb_hilfe": "– ми радо допоможемо.",
}

_KATALOGE = {"en": EN, "uk": UK}


def sprache():
    """Aktive Sprache: ?lang=… (wird gemerkt) > Session > Browser > Deutsch."""
    lang = request.args.get("lang")
    if lang in SPRACHEN:
        session["lang"] = lang
        return lang
    gemerkt = session.get("lang")
    if gemerkt in SPRACHEN:
        return gemerkt
    return request.accept_languages.best_match(list(SPRACHEN)) or "de"


def ue(schluessel, lang=None):
    """Übersetzt einen Text; Fallback ist immer die deutsche Fassung."""
    lang = lang or sprache()
    if lang != "de":
        wert = _KATALOGE.get(lang, {}).get(schluessel)
        if wert is not None:
            return wert
    return DE.get(schluessel, schluessel)
