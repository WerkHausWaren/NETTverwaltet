/* Face ID / Touch ID / Fingerabdruck (WebAuthn-Passkeys) – 20.11.2026.
   Gemeinsam für Login-Seite und Profil. Der private Schlüssel bleibt auf dem Gerät;
   der Server prüft nur die Signatur (app/routes/auth.py, /passkey/...). */
(function () {
  "use strict";

  function b64uZuPuffer(s) {
    s = s.replace(/-/g, "+").replace(/_/g, "/");
    while (s.length % 4) s += "=";
    var bin = atob(s), a = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) a[i] = bin.charCodeAt(i);
    return a.buffer;
  }

  function pufferZuB64u(buf) {
    var a = new Uint8Array(buf), s = "";
    for (var i = 0; i < a.length; i++) s += String.fromCharCode(a[i]);
    return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  function csrf() {
    var m = document.querySelector('meta[name="csrf-token"]');
    if (m) return m.getAttribute("content");
    var f = document.querySelector('input[name="csrf_token"]');
    return f ? f.value : "";
  }

  function senden(url, daten) {
    return fetch(url, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify(daten || {})
    }).then(function (r) {
      return r.json().catch(function () { return { ok: false, fehler: "Unerwartete Antwort (" + r.status + ")." }; });
    });
  }

  function credentialAlsJson(c) {
    var r = c.response, j = {
      id: c.id, rawId: pufferZuB64u(c.rawId), type: c.type,
      clientExtensionResults: (c.getClientExtensionResults && c.getClientExtensionResults()) || {},
      authenticatorAttachment: c.authenticatorAttachment || undefined,
      response: { clientDataJSON: pufferZuB64u(r.clientDataJSON) }
    };
    if (r.attestationObject) {
      j.response.attestationObject = pufferZuB64u(r.attestationObject);
      if (r.getTransports) j.response.transports = r.getTransports();
    } else {
      j.response.authenticatorData = pufferZuB64u(r.authenticatorData);
      j.response.signature = pufferZuB64u(r.signature);
      if (r.userHandle) j.response.userHandle = pufferZuB64u(r.userHandle);
    }
    return j;
  }

  function verstaendlich(err) {
    if (err && err.name === "NotAllowedError") return "Abgebrochen oder nicht bestätigt.";
    if (err && err.name === "InvalidStateError") return "Dieses Gerät ist schon eingerichtet.";
    return (err && err.message) || "Unbekannter Fehler.";
  }

  /** Ist Face ID / Fingerabdruck auf diesem Gerät nutzbar? → Promise<bool> */
  function verfuegbar() {
    if (!window.PublicKeyCredential || !PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable) {
      return Promise.resolve(false);
    }
    return PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable().catch(function () { return false; });
  }

  /** Gerät einrichten (angemeldet). → Promise<{ok, name|fehler}> */
  function einrichten(name) {
    return senden("/passkey/registrieren/optionen").then(function (a) {
      if (!a.ok) return a;
      var o = a.optionen;
      o.challenge = b64uZuPuffer(o.challenge);
      o.user.id = b64uZuPuffer(o.user.id);
      (o.excludeCredentials || []).forEach(function (c) { c.id = b64uZuPuffer(c.id); });
      return navigator.credentials.create({ publicKey: o }).then(function (c) {
        return senden("/passkey/registrieren", { credential: credentialAlsJson(c), name: name || "" });
      }, function (err) { return { ok: false, fehler: verstaendlich(err) }; });
    });
  }

  /** Mit Face ID anmelden. → Promise<{ok, weiter|fehler}> */
  function anmelden(merken, weiter) {
    return senden("/passkey/login/optionen").then(function (a) {
      if (!a.ok) return a;
      var o = a.optionen;
      o.challenge = b64uZuPuffer(o.challenge);
      (o.allowCredentials || []).forEach(function (c) { c.id = b64uZuPuffer(c.id); });
      return navigator.credentials.get({ publicKey: o }).then(function (c) {
        return senden("/passkey/login", { credential: credentialAlsJson(c), merken: merken, weiter: weiter || "" });
      }, function (err) { return { ok: false, fehler: verstaendlich(err) }; });
    });
  }

  window.FaceID = { verfuegbar: verfuegbar, einrichten: einrichten, anmelden: anmelden };
})();
