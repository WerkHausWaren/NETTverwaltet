# -*- coding: utf-8 -*-
"""Mehrsprachiges Portal (Opt-in je Verein über vereine.portal_sprachen).

Übersetzt werden die Mitglieder-App-Navigation, das Schwarze Brett und das
öffentliche Gast-Formular. Alle übrigen Seiten bleiben deutsch, bis sie
Schlüssel aus TEXTE verwenden. Reihenfolge der Übersetzungen in den Tupeln:
de, en, es, uk, ru, ar, fr, vi.
"""
from flask import session, request

SPRACHEN = ["de", "en", "es", "uk", "ru", "ar", "fr", "vi"]
SPRACH_NAMEN = {"de": "Deutsch", "en": "English", "es": "Español",
                "uk": "Українська", "ru": "Русский", "ar": "العربية",
                "fr": "Français", "vi": "Tiếng Việt"}
RTL = {"ar"}

TEXTE = {
    # Navigation der Mitglieder-App
    "nav_start": ("Start", "Home", "Inicio", "Головна", "Главная", "الرئيسية", "Accueil", "Trang chủ"),
    "nav_termine": ("Termine", "Events", "Eventos", "Події", "События", "الفعاليات", "Événements", "Sự kiện"),
    "nav_ueber": ("Über uns", "About us", "Sobre nosotros", "Про нас", "О нас", "من نحن", "À propos", "Về chúng tôi"),
    "nav_schichten": ("Schichten", "Shifts", "Turnos", "Зміни", "Смены", "المناوبات", "Créneaux", "Ca trực"),
    "nav_mitglieder": ("Mitglieder", "Members", "Miembros", "Учасники", "Участники", "الأعضاء", "Membres", "Thành viên"),
    "nav_dateien": ("Dateien", "Files", "Archivos", "Файли", "Файлы", "الملفات", "Fichiers", "Tệp"),
    "nav_ich": ("Ich", "Me", "Yo", "Я", "Я", "أنا", "Moi", "Tôi"),
    "nav_brett": ("Brett", "Board", "Tablón", "Дошка", "Доска", "اللوحة", "Annonces", "Bảng tin"),
    "sprache": ("Sprache", "Language", "Idioma", "Мова", "Язык", "اللغة", "Langue", "Ngôn ngữ"),
    # Schwarzes Brett (Portal + Verwaltung + Gast)
    "brett": ("Schwarzes Brett", "Notice board", "Tablón de anuncios", "Дошка оголошень",
              "Доска объявлений", "لوحة الإعلانات", "Panneau d'annonces", "Bảng tin"),
    "neuer_eintrag": ("＋ Neuer Eintrag", "＋ New entry", "＋ Nueva entrada", "＋ Новий запис",
                      "＋ Новая запись", "＋ إدخال جديد", "＋ Nouvelle entrée", "＋ Mục mới"),
    "art": ("Art", "Type", "Tipo", "Тип", "Тип", "النوع", "Type", "Loại"),
    "ich_suche": ("🙋 Ich suche Hilfe / etwas", "🙋 I'm looking for help / something",
                  "🙋 Busco ayuda / algo", "🙋 Я шукаю допомогу / щось",
                  "🙋 Я ищу помощь / что-то", "🙋 أبحث عن مساعدة / شيء ما",
                  "🙋 Je cherche de l'aide / quelque chose", "🙋 Tôi cần giúp đỡ / tìm gì đó"),
    "ich_biete": ("🎁 Ich biete etwas an", "🎁 I'm offering something", "🎁 Ofrezco algo",
                  "🎁 Я пропоную щось", "🎁 Я предлагаю что-то", "🎁 أقدم شيئاً",
                  "🎁 Je propose quelque chose", "🎁 Tôi muốn giúp đỡ"),
    "eintrag_info": ("📌 Info / Mitteilung", "📌 Info / notice", "📌 Info / aviso",
                     "📌 Інформація", "📌 Информация", "📌 معلومة / إشعار", "📌 Info / annonce", "📌 Thông báo"),
    "kategorie": ("Kategorie", "Category", "Categoría", "Категорія", "Категория", "الفئة", "Catégorie", "Danh mục"),
    "thema": ("Thema", "Topic", "Tema", "Тема", "Тема", "الموضوع", "Sujet", "Chủ đề"),
    "bitte_waehlen": ("– bitte wählen –", "– please choose –", "– elija –", "– оберіть –",
                      "– выберите –", "– اختر –", "– choisissez –", "– chọn –"),
    "titel": ("Titel", "Title", "Título", "Заголовок", "Заголовок", "العنوان", "Titre", "Tiêu đề"),
    "worum": ("Worum geht's?", "What is it about?", "¿De qué se trata?", "Про що йдеться?",
              "О чём речь?", "ما الموضوع؟", "De quoi s'agit-il ?", "Về việc gì?"),
    "details": ("Details … – tippe @ um jemanden zu erwähnen", "Details … – type @ to mention someone",
                "Detalles … – escribe @ para mencionar", "Деталі … – @ щоб згадати когось",
                "Детали … – @ чтобы упомянуть", "التفاصيل …", "Détails … – @ pour mentionner", "Chi tiết …"),
    "text_label": ("Text", "Text", "Texto", "Текст", "Текст", "النص", "Texte", "Nội dung"),
    "meine_sprachen": ("Meine Sprachen (hilft beim Vermitteln)", "My languages (helps with matching)",
                       "Mis idiomas (ayuda a mediar)", "Мої мови (допомагає у посередництві)",
                       "Мои языки (помогает при подборе)", "لغاتي (تساعد في الوساطة)",
                       "Mes langues (utile pour la mise en relation)", "Ngôn ngữ của tôi (giúp kết nối)"),
    "weitere_sprache": ("weitere Sprache …", "another language …", "otro idioma …", "інша мова …",
                        "другой язык …", "لغة أخرى …", "autre langue …", "ngôn ngữ khác …"),
    "veroeffentlichen": ("Veröffentlichen", "Publish", "Publicar", "Опублікувати", "Опубликовать",
                         "نشر", "Publier", "Đăng"),
    "alle": ("Alle", "All", "Todos", "Усі", "Все", "الكل", "Tous", "Tất cả"),
    "von": ("von", "by", "de", "від", "от", "من", "de", "từ"),
    "gast_kennung": ("Gast (Kontakt über die Verwaltung)", "guest (contact via the team)",
                     "invitado (contacto a través del equipo)", "гість (контакт через команду)",
                     "гость (контакт через команду)", "ضيف (التواصل عبر الفريق)",
                     "invité (contact via l'équipe)", "khách (liên hệ qua ban quản lý)"),
    "gemeldet": ("gemeldet", "responded", "respondieron", "відгукнулися", "откликнулись",
                 "استجابوا", "réponses", "đã phản hồi"),
    "ich_melde_mich": ("🙌 Ich melde mich", "🙌 I'll respond", "🙌 Me apunto", "🙌 Я відгукуюсь",
                       "🙌 Я откликаюсь", "🙌 سألبّي", "🙌 Je réponds", "🙌 Tôi sẽ giúp"),
    "du_gemeldet": ("✓ Du hast dich gemeldet", "✓ You responded", "✓ Te has apuntado",
                    "✓ Ти відгукнувся/-лася", "✓ Вы откликнулись", "✓ لقد لبّيت",
                    "✓ Tu as répondu", "✓ Bạn đã phản hồi"),
    "entfernen": ("✕ entfernen", "✕ remove", "✕ eliminar", "✕ видалити", "✕ удалить",
                  "✕ إزالة", "✕ supprimer", "✕ xóa"),
    "wartet_heute": ("⏳ wartet seit heute", "⏳ waiting since today", "⏳ espera desde hoy",
                     "⏳ чекає з сьогодні", "⏳ ждёт с сегодня", "⏳ ينتظر منذ اليوم",
                     "⏳ en attente depuis aujourd'hui", "⏳ chờ từ hôm nay"),
    "wartet_seit": ("⏳ wartet seit", "⏳ waiting for", "⏳ espera desde hace", "⏳ чекає вже",
                    "⏳ ждёт уже", "⏳ ينتظر منذ", "⏳ en attente depuis", "⏳ đã chờ"),
    "tag": ("Tag", "day", "día", "день", "день", "يوم", "jour", "ngày"),
    "tagen": ("Tagen", "days", "días", "дн.", "дн.", "أيام", "jours", "ngày"),
    "leer": ("Noch nichts am Schwarzen Brett.", "Nothing on the notice board yet.",
             "Aún no hay nada en el tablón.", "На дошці поки нічого немає.",
             "На доске пока ничего нет.", "لا شيء على اللوحة بعد.",
             "Rien sur le panneau pour l'instant.", "Chưa có gì trên bảng tin."),
    "posten_zuordnung": ("Zum Posten muss dein Konto einem Mitglied zugeordnet sein (siehe „Ich“).",
                         "To post, your account must be linked to a member (see “Me”).",
                         "Para publicar, tu cuenta debe estar vinculada a un miembro (ver «Yo»).",
                         "Щоб публікувати, ваш акаунт має бути пов'язаний з учасником (див. «Я»).",
                         "Чтобы публиковать, ваш аккаунт должен быть связан с участником (см. «Я»).",
                         "للنشر يجب ربط حسابك بعضو (انظر «أنا»).",
                         "Pour publier, votre compte doit être lié à un membre (voir « Moi »).",
                         "Để đăng, tài khoản của bạn phải được liên kết với một thành viên (xem “Tôi”)."),
    # Gast-Formular
    "gast_untertitel": ("Hilfe suchen oder Hilfe anbieten – ganz ohne Konto. Damit wir dich erreichen können, brauchen wir Name, Kontakt und deine Sprachen. Dein Eintrag wird vor der Veröffentlichung vom Team geprüft; deine Kontaktdaten erscheinen nicht öffentlich.",
                       "Ask for help or offer help – no account needed. So we can reach you, we need your name, contact details and languages. Your entry is reviewed by the team before publication; your contact details are never shown publicly.",
                       "Pedir ayuda u ofrecer ayuda – sin cuenta. Para poder contactarte necesitamos tu nombre, contacto e idiomas. El equipo revisa tu entrada antes de publicarla; tus datos de contacto no se muestran públicamente.",
                       "Шукати допомогу або запропонувати її – без акаунта. Щоб ми могли зв'язатися, потрібні ім'я, контакт і ваші мови. Команда перевіряє запис перед публікацією; контактні дані не з'являються публічно.",
                       "Искать помощь или предложить её – без аккаунта. Чтобы мы могли связаться, нужны имя, контакт и ваши языки. Команда проверяет запись перед публикацией; контактные данные не показываются публично.",
                       "اطلب المساعدة أو قدّمها – دون حساب. لكي نتواصل معك نحتاج اسمك ووسيلة الاتصال ولغاتك. يراجع الفريق مدخلتك قبل النشر؛ ولا تظهر بيانات الاتصال علناً.",
                       "Demander ou proposer de l'aide – sans compte. Pour vous joindre, il nous faut votre nom, un contact et vos langues. Votre entrée est vérifiée avant publication ; vos coordonnées ne sont jamais publiques.",
                       "Tìm hoặc đề nghị giúp đỡ – không cần tài khoản. Để liên hệ, chúng tôi cần tên, thông tin liên lạc và ngôn ngữ của bạn. Ban quản lý duyệt trước khi đăng; thông tin liên lạc không hiển thị công khai."),
    "ich_moechte": ("Ich möchte …", "I would like to …", "Quiero …", "Я хочу …", "Я хочу …",
                    "أريد أن …", "Je souhaite …", "Tôi muốn …"),
    "gast_suchen": ("🙋 Hilfe suchen", "🙋 ask for help", "🙋 pedir ayuda", "🙋 шукати допомогу",
                    "🙋 искать помощь", "🙋 طلب المساعدة", "🙋 demander de l'aide", "🙋 tìm giúp đỡ"),
    "gast_bieten": ("🎁 Hilfe anbieten", "🎁 offer help", "🎁 ofrecer ayuda", "🎁 запропонувати допомогу",
                    "🎁 предложить помощь", "🎁 تقديم المساعدة", "🎁 proposer de l'aide", "🎁 giúp đỡ"),
    "worum_beispiel": ("z.B. Suche Begleitung zum Amt", "e.g. Looking for someone to accompany me to the office",
                       "p. ej. Busco acompañamiento a la oficina", "напр. Шукаю супровід до установи",
                       "напр. Ищу сопровождение в ведомство", "مثلاً: أبحث عن مرافقة إلى الدائرة",
                       "p. ex. Cherche un accompagnement à l'administration", "vd: Cần người đi cùng đến cơ quan"),
    "details_frage": ("Was genau, wann, wo?", "What exactly, when, where?", "¿Qué, cuándo, dónde?",
                      "Що саме, коли, де?", "Что именно, когда, где?", "ماذا بالضبط، متى، أين؟",
                      "Quoi, quand, où ?", "Cụ thể gì, khi nào, ở đâu?"),
    "dein_name": ("Dein Name", "Your name", "Tu nombre", "Ваше ім'я", "Ваше имя", "اسمك",
                  "Votre nom", "Tên của bạn"),
    "kontakt_label": ("So erreichen wir dich (Telefon oder E-Mail)", "How we can reach you (phone or e-mail)",
                      "Cómo contactarte (teléfono o correo)", "Як з вами зв'язатися (телефон або e-mail)",
                      "Как с вами связаться (телефон или e-mail)", "كيف نصل إليك (هاتف أو بريد إلكتروني)",
                      "Comment vous joindre (téléphone ou e-mail)", "Cách liên hệ với bạn (điện thoại hoặc e-mail)"),
    "welche_sprachen": ("Welche Sprachen sprichst du?", "Which languages do you speak?",
                        "¿Qué idiomas hablas?", "Якими мовами ви розмовляєте?",
                        "На каких языках вы говорите?", "ما اللغات التي تتحدثها؟",
                        "Quelles langues parlez-vous ?", "Bạn nói những ngôn ngữ nào?"),
    "absenden": ("Eintrag absenden", "Submit entry", "Enviar entrada", "Надіслати запис",
                 "Отправить запись", "إرسال المدخلة", "Envoyer l'entrée", "Gửi"),
    "danke": ("Danke!", "Thank you!", "¡Gracias!", "Дякуємо!", "Спасибо!", "شكراً!", "Merci !", "Cảm ơn!"),
    "danke_text": ("Dein Eintrag ist angekommen. Unser Team schaut kurz drüber und schaltet ihn frei – wir melden uns über deine angegebene Kontaktmöglichkeit, sobald es passende Nachrichten gibt.",
                   "Your entry has been received. Our team will review and publish it – we will contact you via the details you provided as soon as there is news.",
                   "Tu entrada ha llegado. El equipo la revisará y publicará – te contactaremos a través de tus datos en cuanto haya novedades.",
                   "Ваш запис отримано. Команда перегляне і опублікує його – ми зв'яжемося з вами за вказаним контактом, щойно будуть новини.",
                   "Ваша запись получена. Команда проверит и опубликует её – мы свяжемся с вами по указанному контакту, как только будут новости.",
                   "وصلت مدخلتك. سيراجعها الفريق وينشرها – وسنتواصل معك عبر بيانات الاتصال عند وجود جديد.",
                   "Votre entrée a bien été reçue. L'équipe va la vérifier et la publier – nous vous contacterons dès qu'il y aura du nouveau.",
                   "Đã nhận được mục của bạn. Ban quản lý sẽ duyệt và đăng – chúng tôi sẽ liên hệ qua thông tin bạn cung cấp khi có tin mới."),
    "weiterer_eintrag": ("Weiteren Eintrag schreiben", "Write another entry", "Escribir otra entrada",
                         "Написати ще один запис", "Написать ещё одну запись", "كتابة مدخلة أخرى",
                         "Écrire une autre entrée", "Viết mục khác"),
    "pflicht_fehlt": ("Bitte noch ausfüllen", "Please also fill in", "Falta completar",
                      "Будь ласка, заповніть", "Пожалуйста, заполните", "يرجى تعبئة",
                      "Merci de compléter", "Vui lòng điền"),
    "zurueck": ("← Zurück", "← Back", "← Atrás", "← Назад", "← Назад", "→ رجوع", "← Retour", "← Quay lại"),
    "anmelden": ("Anmelden (Mitglieder)", "Log in (members)", "Iniciar sesión (miembros)",
                 "Увійти (для учасників)", "Войти (для участников)", "تسجيل الدخول (للأعضاء)",
                 "Connexion (membres)", "Đăng nhập (thành viên)"),
    "link_hinweis": ("Wichtig: Mit diesem persönlichen Link kannst du deinen Eintrag später ändern oder zurückziehen – bitte gut aufbewahren:",
                     "Important: with this personal link you can edit or withdraw your entry later – please keep it safe:",
                     "Importante: con este enlace personal puedes editar o retirar tu entrada más tarde – guárdalo bien:",
                     "Важливо: за цим особистим посиланням ви зможете змінити або відкликати запис – збережіть його:",
                     "Важно: по этой личной ссылке вы сможете изменить или отозвать запись – сохраните её:",
                     "مهم: بهذا الرابط الشخصي يمكنك تعديل مدخلتك أو سحبها لاحقاً – احتفظ به جيداً:",
                     "Important : avec ce lien personnel vous pourrez modifier ou retirer votre entrée – conservez-le bien :",
                     "Quan trọng: với liên kết cá nhân này bạn có thể sửa hoặc rút lại mục của mình – hãy lưu giữ cẩn thận:"),
    "eintrag_bearbeiten": ("Eintrag bearbeiten", "Edit entry", "Editar entrada", "Змінити запис",
                           "Изменить запись", "تعديل المدخلة", "Modifier l'entrée", "Sửa mục"),
    "speichern": ("Speichern", "Save", "Guardar", "Зберегти", "Сохранить", "حفظ", "Enregistrer", "Lưu"),
    "zurueckziehen": ("Eintrag zurückziehen", "Withdraw entry", "Retirar entrada", "Відкликати запис",
                      "Отозвать запись", "سحب المدخلة", "Retirer l'entrée", "Rút lại mục"),
    "aktuell_danke": ("Danke! Dein Eintrag bleibt aktiv.", "Thank you! Your posting stays active.",
                      "¡Gracias! Tu anuncio sigue activo.",
                      "Дякуємо! Ваше оголошення залишається активним.",
                      "Спасибо! Ваше объявление остаётся активным.",
                      "شكراً! سيبقى إعلانك نشطاً.",
                      "Merci ! Votre annonce reste active.",
                      "Cảm ơn! Tin của bạn vẫn còn hiệu lực."),
    "aktuell_ungueltig": ("Dieser Link ist nicht mehr gültig.", "This link is no longer valid.",
                          "Este enlace ya no es válido.",
                          "Це посилання більше не дійсне.",
                          "Эта ссылка больше не действительна.",
                          "هذا الرابط لم يعد صالحاً.",
                          "Ce lien n'est plus valide.",
                          "Liên kết này không còn hiệu lực."),
    "zurueckgezogen": ("Dein Eintrag wurde zurückgezogen.", "Your entry has been withdrawn.",
                       "Tu entrada ha sido retirada.", "Ваш запис відкликано.",
                       "Ваша запись отозвана.", "تم سحب مدخلتك.",
                       "Votre entrée a été retirée.", "Mục của bạn đã được rút lại."),
    "nach_pruefung": ("Gespeichert! Das Team prüft deine Änderung kurz, bevor der Eintrag wieder erscheint.",
                      "Saved! The team will briefly review your change before the entry appears again.",
                      "¡Guardado! El equipo revisará el cambio antes de que la entrada vuelva a aparecer.",
                      "Збережено! Команда перегляне зміну, перш ніж запис з'явиться знову.",
                      "Сохранено! Команда проверит изменение, прежде чем запись появится снова.",
                      "تم الحفظ! سيراجع الفريق التعديل قبل ظهور المدخلة مجدداً.",
                      "Enregistré ! L'équipe vérifiera la modification avant que l'entrée réapparaisse.",
                      "Đã lưu! Ban quản lý sẽ duyệt thay đổi trước khi mục xuất hiện lại."),
    "k_aufgaben": ("Meine Aufgaben", "My tasks", "Mis tareas", "Мої завдання", "Мои задачи",
                   "مهامي", "Mes tâches", "Nhiệm vụ của tôi"),
    "k_reservierungen": ("Meine Reservierungen", "My reservations", "Mis reservas",
                         "Мої бронювання", "Мои бронирования", "حجوزاتي",
                         "Mes réservations", "Đặt chỗ của tôi"),
    "k_ankuendigungen": ("Ankündigungen", "Announcements", "Anuncios", "Оголошення",
                         "Объявления", "الإعلانات", "Annonces", "Thông báo"),
    "k_geburtstage": ("Geburtstage", "Birthdays", "Cumpleaños", "Дні народження",
                      "Дни рождения", "أعياد الميلاد", "Anniversaires", "Sinh nhật"),
    "k_infos": ("Infos & FAQ", "Info & FAQ", "Información y FAQ", "Інформація та FAQ",
                "Информация и FAQ", "معلومات وأسئلة شائعة", "Infos & FAQ", "Thông tin & FAQ"),
    "k_galerie": ("Fotogalerie", "Photo gallery", "Galería de fotos", "Фотогалерея",
                  "Фотогалерея", "معرض الصور", "Galerie photos", "Thư viện ảnh"),
    "k_beitrag": ("Mein Beitrag", "My membership fee", "Mi cuota", "Мій внесок",
                  "Мой взнос", "اشتراكي", "Ma cotisation", "Phí hội viên của tôi"),
    "k_termine_naechste": ("Nächste Termine", "Upcoming events", "Próximos eventos",
                           "Найближчі події", "Ближайшие события", "الفعاليات القادمة",
                           "Prochains événements", "Sự kiện sắp tới"),
    "k_mitglied_werden": ("Lust, Mitglied zu werden?", "Would you like to become a member?",
                          "¿Quieres hacerte miembro?", "Хочете стати учасником?",
                          "Хотите стать участником?", "هل تودّ أن تصبح عضواً؟",
                          "Envie de devenir membre ?", "Bạn muốn trở thành thành viên?"),
    "k_umfragen": ("Umfragen & Terminfindung", "Polls & scheduling", "Encuestas y fechas",
                   "Опитування та вибір дати", "Опросы и выбор даты", "استطلاعات ومواعيد",
                   "Sondages & dates", "Khảo sát & chọn ngày"),
    "k_daten": ("Meine Daten", "My details", "Mis datos", "Мої дані", "Мои данные",
                "بياناتي", "Mes données", "Thông tin của tôi"),
    "k_kommwege": ("Kommunikationswege", "Communication channels", "Vías de contacto",
                   "Канали зв'язку", "Каналы связи", "قنوات التواصل",
                   "Canaux de communication", "Kênh liên lạc"),
    "k_mandat": ("Lastschriftmandat", "Direct debit mandate", "Mandato de domiciliación",
                 "Мандат на списання", "Мандат на списание", "تفويض الخصم المباشر",
                 "Mandat de prélèvement", "Ủy nhiệm chi"),
    "k_datenschutz": ("Datenschutz & Sichtbarkeit", "Privacy & visibility", "Privacidad y visibilidad",
                      "Приватність і видимість", "Приватность и видимость", "الخصوصية والظهور",
                      "Confidentialité et visibilité", "Quyền riêng tư & hiển thị"),
    "k_app_install": ("Als App installieren", "Install as an app", "Instalar como app",
                      "Встановити як застосунок", "Установить как приложение",
                      "التثبيت كتطبيق", "Installer comme appli", "Cài như ứng dụng"),
    "k_hilfe": ("Hilfe", "Help", "Ayuda", "Допомога", "Помощь", "مساعدة", "Aide", "Trợ giúp"),
    "k_meine_schichten": ("Meine Schichten", "My shifts", "Mis turnos", "Мої зміни",
                          "Мои смены", "مناوباتي", "Mes créneaux", "Ca của tôi"),
    "k_offene_schichten": ("Offene & zu tauschende Schichten", "Open shifts & swaps",
                           "Turnos libres y cambios", "Вільні зміни та обміни",
                           "Свободные смены и обмены", "مناوبات شاغرة وتبادل",
                           "Créneaux libres & échanges", "Ca trống & đổi ca"),
    "k_berichte": ("Berichte & Besucher:innen", "Reports & visitors", "Informes y visitantes",
                   "Звіти та відвідувачі", "Отчёты и посетители", "التقارير والزوار",
                   "Rapports & visiteurs", "Báo cáo & khách"),
    "k_berichte_sub": ("Rückmeldungen und Besucher:innen-Zahlen der Schichten",
                       "Feedback and visitor numbers of the shifts",
                       "Comentarios y número de visitantes de los turnos",
                       "Відгуки та кількість відвідувачів змін",
                       "Отзывы и число посетителей смен",
                       "الملاحظات وأعداد زوار المناوبات",
                       "Retours et fréquentation des créneaux",
                       "Phản hồi và số khách của các ca"),
    "k_rueckmeldungen": ("Rückmeldungen", "Feedback", "Comentarios", "Відгуки", "Отзывы",
                         "الملاحظات", "Retours", "Phản hồi"),
    "paar_verbinden": ("Ausgewähltes Gesuch & Angebot verbinden", "Connect selected request & offer",
                       "Conectar solicitud y oferta seleccionadas", "З'єднати обране прохання та пропозицію",
                       "Соединить выбранные запрос и предложение", "ربط الطلب والعرض المحددين",
                       "Relier la demande et l'offre sélectionnées", "Kết nối yêu cầu & đề nghị đã chọn"),
}


def aktuelle_sprache():
    lang = session.get("sprache", "de")
    return lang if lang in SPRACHEN else "de"


def t(schluessel):
    eintrag = TEXTE.get(schluessel)
    if not eintrag:
        return schluessel
    try:
        wert = eintrag[SPRACHEN.index(aktuelle_sprache())]
    except (ValueError, IndexError):
        wert = ""
    return wert or eintrag[0]


def richtung():
    return "rtl" if aktuelle_sprache() in RTL else "ltr"

# Übersetzung der Standard-Kategorien des Schwarzen Bretts.
# Anzeige wird übersetzt, gespeichert/gefiltert wird immer der deutsche Wert.
KATEGORIEN = {
    "Wohnen": ("Wohnen", "Housing", "Vivienda", "Житло", "Жильё", "السكن", "Logement", "Nhà ở"),
    "Sprache & Übersetzung": ("Sprache & Übersetzung", "Language & translation", "Idioma y traducción",
                              "Мова та переклад", "Язык и перевод", "اللغة والترجمة",
                              "Langue et traduction", "Ngôn ngữ & phiên dịch"),
    "Begleitung Amt/Arzt": ("Begleitung Amt/Arzt", "Accompanying (authorities/doctor)",
                            "Acompañamiento (oficinas/médico)", "Супровід (установи/лікар)",
                            "Сопровождение (учреждения/врач)", "مرافقة (دوائر/طبيب)",
                            "Accompagnement (administration/médecin)", "Đi cùng (cơ quan/bác sĩ)"),
    "Möbel & Sachspenden": ("Möbel & Sachspenden", "Furniture & donations", "Muebles y donaciones",
                            "Меблі та речі", "Мебель и вещи", "أثاث وتبرعات",
                            "Meubles et dons", "Đồ đạc & quyên góp"),
    "Kinder & Schule": ("Kinder & Schule", "Children & school", "Niños y escuela",
                        "Діти та школа", "Дети и школа", "الأطفال والمدرسة",
                        "Enfants et école", "Trẻ em & trường học"),
    "Mobilität": ("Mobilität", "Mobility", "Movilidad", "Транспорт", "Транспорт",
                  "التنقل", "Mobilité", "Đi lại"),
    "Arbeit": ("Arbeit", "Work", "Trabajo", "Робота", "Работа", "العمل", "Travail", "Việc làm"),
    "Sonstiges": ("Sonstiges", "Other", "Otros", "Інше", "Другое", "أخرى", "Autre", "Khác"),
}


def tk(kategorie):
    """Kategorie-Namen übersetzen (unbekannte Namen bleiben unverändert)."""
    eintrag = KATEGORIEN.get((kategorie or "").strip())
    if not eintrag:
        return kategorie
    try:
        wert = eintrag[SPRACHEN.index(aktuelle_sprache())]
    except (ValueError, IndexError):
        wert = ""
    return wert or kategorie

# Sprachen, die Menschen sprechen können (Brett-Einträge, Mitglieder-Stammdaten).
SPRECH_SPRACHEN = ["Deutsch", "English", "Español", "Українська", "Русский",
                   "العربية", "Français", "Tiếng Việt"]

# Kurze Labels für die Anzeige (Mitgliederliste, Detailseite). Deckt die
# Auswahl-Sprachen samt deutscher Schreibweisen und gängige weitere ab;
# Unbekanntes fällt auf die ersten zwei Großbuchstaben zurück.
SPRACH_KUERZEL = {
    "deutsch": "DE", "english": "EN", "englisch": "EN", "español": "ES",
    "spanisch": "ES", "українська": "UA", "ukrainisch": "UA", "русский": "RU",
    "russisch": "RU", "العربية": "AR", "arabisch": "AR", "français": "FR",
    "französisch": "FR", "tiếng việt": "VI", "vietnamesisch": "VI",
    "polnisch": "PL", "polski": "PL", "türkisch": "TR", "türkçe": "TR",
    "persisch": "FA", "farsi": "FA", "dari": "FA", "kurdisch": "KU",
    "paschtu": "PS", "pashto": "PS", "tigrinja": "TI", "tigrinya": "TI",
    "somali": "SO", "italienisch": "IT", "portugiesisch": "PT",
    "niederländisch": "NL", "schwedisch": "SV", "tschechisch": "CS",
    "rumänisch": "RO", "bulgarisch": "BG", "griechisch": "EL",
    "gebärdensprache": "DGS",
}


def sprach_kuerzel(name):
    """Kurz-Label einer Sprache, z.B. „Ukrainisch" -> „UA"."""
    name = (name or "").strip()
    if not name:
        return ""
    return SPRACH_KUERZEL.get(name.lower(), name[:2].upper())
