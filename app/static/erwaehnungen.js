/* @-Erwähnungen: an jedem Feld mit Klasse "mention" eine Nutzer-Auswahl beim Tippen von "@".
   Die Nutzerliste kommt aus window.MENTION_USERS = [{id, name}, ...]. */
(function () {
    var users = window.MENTION_USERS || [];
    if (!users.length) return;
    var felder = document.querySelectorAll("textarea.mention, input.mention");
    Array.prototype.forEach.call(felder, verbinde);

    function verbinde(feld) {
        var eltern = feld.parentNode;
        if (getComputedStyle(eltern).position === "static") eltern.style.position = "relative";
        var box = document.createElement("div");
        box.className = "mention-box";
        box.style.display = "none";
        feld.insertAdjacentElement("afterend", box);
        var treffer = [], aktiv = 0;

        function schliessen() { box.style.display = "none"; }
        function abfrage() {
            var pos = feld.selectionStart;
            var vor = feld.value.slice(0, pos);
            var at = vor.lastIndexOf("@");
            if (at < 0) return null;
            if (at > 0 && !/\s/.test(vor[at - 1])) return null;   // "@" nur am Wortanfang
            var q = vor.slice(at + 1);
            if (/\n/.test(q)) return null;
            return { at: at, q: q, pos: pos };
        }
        function zeigen() {
            var info = abfrage();
            if (!info) return schliessen();
            var ql = info.q.toLowerCase();
            treffer = users.filter(function (u) { return u.name.toLowerCase().indexOf(ql) >= 0; }).slice(0, 6);
            if (!treffer.length) return schliessen();
            aktiv = 0;
            // Namen als Text einsetzen, nicht als HTML – ein Mitgliedsname aus
            // einem Import darf keine Schaltflächen oder Skripte erzeugen.
            box.textContent = "";
            treffer.forEach(function (u, i) {
                var zeile = document.createElement("div");
                zeile.className = "mention-opt" + (i === 0 ? " an" : "");
                zeile.setAttribute("data-i", i);
                zeile.textContent = "@" + u.name;
                box.appendChild(zeile);
            });
            box.style.display = "block";
        }
        function markiere() {
            Array.prototype.forEach.call(box.children, function (c, i) { c.classList.toggle("an", i === aktiv); });
        }
        function einfuegen(i) {
            var info = abfrage(); var u = treffer[i];
            if (!info || !u) return;
            var vor = feld.value.slice(0, info.at);
            var nach = feld.value.slice(info.pos);
            var einf = "@" + u.name + " ";
            feld.value = vor + einf + nach;
            var neu = (vor + einf).length;
            feld.setSelectionRange(neu, neu);
            schliessen(); feld.focus();
        }
        feld.addEventListener("input", zeigen);
        feld.addEventListener("keydown", function (e) {
            if (box.style.display === "none") return;
            if (e.key === "ArrowDown") { e.preventDefault(); aktiv = Math.min(aktiv + 1, treffer.length - 1); markiere(); }
            else if (e.key === "ArrowUp") { e.preventDefault(); aktiv = Math.max(aktiv - 1, 0); markiere(); }
            else if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); einfuegen(aktiv); }
            else if (e.key === "Escape") { schliessen(); }
        });
        box.addEventListener("mousedown", function (e) {
            var opt = e.target.closest(".mention-opt");
            if (!opt) return;
            e.preventDefault();
            einfuegen(parseInt(opt.getAttribute("data-i"), 10));
        });
        feld.addEventListener("blur", function () { setTimeout(schliessen, 150); });
    }
})();
