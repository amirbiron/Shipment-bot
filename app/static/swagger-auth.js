/**
 * swagger-auth.js — ווידג'ט כניסה מהירה ל-Swagger UI
 *
 * מאפשר הזנת Admin API Key ו-JWT (דרך OTP) ישירות מדף הדוקומנטציה,
 * ללא צורך לקרוא ידנית ל-endpoints של האימות.
 */
(function () {
  "use strict";

  /* ── המתנה לטעינת Swagger UI ── */
  var poll = setInterval(function () {
    if (window.ui && document.querySelector(".swagger-ui")) {
      clearInterval(poll);
      buildWidget();
    }
  }, 150);

  var _phone = "";
  var _pendingStations = null;

  /* ── helpers ── */
  function _$(id) {
    return document.getElementById(id);
  }

  function setMsg(id, text, cls) {
    var el = _$(id);
    el.textContent = text;
    el.className = "qa-msg " + (cls || "");
  }

  /** מניעת XSS — escaping של תווים מיוחדים ב-HTML (כולל מירכאות לשימוש ב-attributes) */
  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  /* ── בניית הווידג'ט ── */
  function buildWidget() {
    var el = document.createElement("div");
    el.id = "qa";
    el.innerHTML = widgetHTML();
    var swagger = document.querySelector(".swagger-ui");
    swagger.parentNode.insertBefore(el, swagger);

    // toggle
    _$("qa-toggle").addEventListener("click", function () {
      var body = _$("qa-body");
      var arrow = _$("qa-arrow");
      body.classList.toggle("qa-collapsed");
      arrow.textContent = body.classList.contains("qa-collapsed") ? "◀" : "▼";
    });

    // Enter key
    _$("qa-api-key").addEventListener("keydown", function (e) {
      if (e.key === "Enter") onSetAdminKey();
    });
    _$("qa-phone").addEventListener("keydown", function (e) {
      if (e.key === "Enter") onRequestOTP();
    });
    _$("qa-otp").addEventListener("keydown", function (e) {
      if (e.key === "Enter") onVerifyOTP();
    });
  }

  /* ── HTML ── */
  function widgetHTML() {
    return (
      "<style>" +
      "#qa{background:#1b1b1b;color:#e0e0e0;border-bottom:2px solid #49cc90;" +
      "font-family:system-ui,-apple-system,'Noto Sans Hebrew',sans-serif;direction:rtl}" +
      "#qa .qa-hdr{display:flex;justify-content:space-between;align-items:center;" +
      "padding:10px 20px;cursor:pointer}" +
      "#qa .qa-hdr span:first-child{font-size:14px;font-weight:700;color:#49cc90}" +
      "#qa .qa-body{display:flex;gap:16px;flex-wrap:wrap;padding:0 20px 14px}" +
      "#qa .qa-body.qa-collapsed{display:none}" +
      "#qa .qa-sec{flex:1;min-width:260px;background:#262626;padding:12px;border-radius:6px}" +
      "#qa .qa-sec h4{margin:0 0 8px;font-size:13px;color:#9a9a9a;font-weight:400}" +
      "#qa .qa-row{display:flex;gap:8px;align-items:center}" +
      "#qa input[type=text]{flex:1;padding:6px 10px;border:1px solid #444;border-radius:4px;" +
      "background:#1a1a1a;color:#fff;font-size:13px;direction:ltr;text-align:left;" +
      "box-sizing:border-box}" +
      "#qa button{padding:6px 14px;border:none;border-radius:4px;cursor:pointer;" +
      "font-size:13px;white-space:nowrap}" +
      "#qa .qa-btn{background:#49cc90;color:#1b1b1b;font-weight:700}" +
      "#qa .qa-btn:hover{background:#3eb882}" +
      "#qa .qa-btn:disabled{opacity:.4;cursor:not-allowed}" +
      "#qa .qa-msg{font-size:12px;margin-top:6px;min-height:16px}" +
      "#qa .qa-ok{color:#49cc90}" +
      "#qa .qa-err{color:#ff6b6b}" +
      "#qa .qa-info{color:#61affe}" +
      "#qa .qa-stations{margin-top:8px}" +
      "#qa .qa-stations label{display:block;padding:4px 0;cursor:pointer;font-size:13px}" +
      "#qa .qa-stations input[type=radio]{margin-left:6px}" +
      "</style>" +
      '<div class="qa-hdr" id="qa-toggle">' +
      "<span>\u05db\u05e0\u05d9\u05e1\u05d4 \u05de\u05d4\u05d9\u05e8\u05d4 \u05dc-API</span>" +
      '<span id="qa-arrow">\u25BC</span>' +
      "</div>" +
      '<div class="qa-body" id="qa-body">' +
      /* Admin API Key */
      '<div class="qa-sec">' +
      "<h4>Admin API Key</h4>" +
      '<div class="qa-row">' +
      '<input id="qa-api-key" type="text" placeholder="ADMIN_API_KEY">' +
      '<button class="qa-btn" id="qa-key-btn">\u05d4\u05d2\u05d3\u05e8</button>' +
      "</div>" +
      '<div class="qa-msg" id="qa-key-msg"></div>' +
      "</div>" +
      /* OTP Login */
      '<div class="qa-sec">' +
      "<h4>JWT \u2014 \u05db\u05e0\u05d9\u05e1\u05d4 \u05e2\u05dd OTP</h4>" +
      '<div class="qa-row" id="qa-step1">' +
      '<input id="qa-phone" type="text" placeholder="\u05de\u05e1\u05e4\u05e8 \u05d8\u05dc\u05e4\u05d5\u05df">' +
      '<button class="qa-btn" id="qa-send">\u05e9\u05dc\u05d7 OTP</button>' +
      "</div>" +
      '<div class="qa-row" id="qa-step2" style="display:none;margin-top:8px">' +
      '<input id="qa-otp" type="text" placeholder="\u05e7\u05d5\u05d3 OTP (6 \u05e1\u05e4\u05e8\u05d5\u05ea)" maxlength="6">' +
      '<button class="qa-btn" id="qa-verify">\u05d0\u05de\u05ea</button>' +
      "</div>" +
      '<div id="qa-stations" class="qa-stations" style="display:none"></div>' +
      '<div class="qa-msg" id="qa-otp-msg"></div>' +
      "</div>" +
      "</div>"
    );
  }

  /* ── Admin API Key ── */
  function onSetAdminKey() {
    var key = _$("qa-api-key").value.trim();
    if (!key) {
      setMsg("qa-key-msg", "\u05d9\u05e9 \u05dc\u05d4\u05d6\u05d9\u05df \u05de\u05e4\u05ea\u05d7", "qa-err");
      return;
    }
    window.ui.preauthorizeApiKey("APIKeyHeader", key);
    setMsg("qa-key-msg", "\u2713 \u05de\u05e4\u05ea\u05d7 API \u05d4\u05d5\u05d2\u05d3\u05e8", "qa-ok");
  }

  /* ── Request OTP ── */
  async function onRequestOTP() {
    var phone = _$("qa-phone").value.trim();
    if (!phone) {
      setMsg("qa-otp-msg", "\u05d9\u05e9 \u05dc\u05d4\u05d6\u05d9\u05df \u05de\u05e1\u05e4\u05e8 \u05d8\u05dc\u05e4\u05d5\u05df", "qa-err");
      return;
    }
    _$("qa-send").disabled = true;
    setMsg("qa-otp-msg", "\u05e9\u05d5\u05dc\u05d7\u2026", "qa-info");
    try {
      var r = await fetch("/api/panel/auth/request-otp", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ phone_number: phone }),
      });
      var d = await r.json();
      if (r.ok) {
        _phone = phone;
        // איפוס מצב תחנות מבקשה קודמת (מונע שליחת station_id ישן)
        _pendingStations = null;
        _$("qa-stations").innerHTML = "";
        _$("qa-stations").style.display = "none";
        _$("qa-otp").value = "";
        _$("qa-step2").style.display = "flex";
        _$("qa-otp").focus();
        setMsg(
          "qa-otp-msg",
          "\u05e7\u05d5\u05d3 \u05e0\u05e9\u05dc\u05d7 \u2014 \u05d1\u05d3\u05d5\u05e7 \u05d1\u05d4\u05d5\u05d3\u05e2\u05d5\u05ea \u05d4\u05d1\u05d5\u05d8 (\u05d0\u05d5 \u05d1\u05dc\u05d5\u05d2\u05d9\u05dd)",
          "qa-ok"
        );
      } else {
        setMsg("qa-otp-msg", d.detail || "\u05e9\u05d2\u05d9\u05d0\u05d4 \u05d1\u05e9\u05dc\u05d9\u05d7\u05ea OTP", "qa-err");
      }
    } catch (e) {
      setMsg("qa-otp-msg", "\u05e9\u05d2\u05d9\u05d0\u05ea \u05e8\u05e9\u05ea: " + e.message, "qa-err");
    }
    _$("qa-send").disabled = false;
  }

  /* ── Verify OTP ── */
  async function onVerifyOTP() {
    var otp = _$("qa-otp").value.trim();
    if (!otp || otp.length !== 6) {
      setMsg(
        "qa-otp-msg",
        "\u05e7\u05d5\u05d3 OTP \u05d7\u05d9\u05d9\u05d1 \u05dc\u05d4\u05d9\u05d5\u05ea 6 \u05e1\u05e4\u05e8\u05d5\u05ea",
        "qa-err"
      );
      return;
    }

    // אם המשתמש בחר תחנה מהרשימה
    var stationId = null;
    if (_pendingStations) {
      var checked = document.querySelector(
        "#qa-stations input[type=radio]:checked"
      );
      if (!checked) {
        setMsg("qa-otp-msg", "\u05d9\u05e9 \u05dc\u05d1\u05d7\u05d5\u05e8 \u05ea\u05d7\u05e0\u05d4", "qa-err");
        return;
      }
      stationId = parseInt(checked.value, 10);
    }

    _$("qa-verify").disabled = true;
    setMsg("qa-otp-msg", "\u05de\u05d0\u05de\u05ea\u2026", "qa-info");
    try {
      var body = { phone_number: _phone, otp: otp };
      if (stationId !== null) body.station_id = stationId;
      var r = await fetch("/api/panel/auth/verify-otp", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      var d = await r.json();

      if (r.ok && d.access_token) {
        // הגדרת Bearer token ב-Swagger UI
        window.ui.authActions.authorize({
          HTTPBearer: {
            name: "HTTPBearer",
            schema: { type: "http", scheme: "bearer" },
            value: d.access_token,
          },
        });
        _pendingStations = null;
        _$("qa-stations").style.display = "none";
        // setMsg משתמש ב-textContent ולכן בטוח מ-XSS
        setMsg(
          "qa-otp-msg",
          "\u2713 \u05de\u05d7\u05d5\u05d1\u05e8! \u05ea\u05d7\u05e0\u05d4: " +
            String(d.station_name) +
            " (ID: " +
            String(d.station_id) +
            ")",
          "qa-ok"
        );
      } else if (r.ok && d.choose_station) {
        // ריבוי תחנות — הצגת בורר
        _pendingStations = d.stations;
        var html = "<strong>\u05d1\u05d7\u05e8 \u05ea\u05d7\u05e0\u05d4:</strong>";
        d.stations.forEach(function (s) {
          var safeId = escapeHtml(s.station_id);
          var safeName = escapeHtml(s.station_name);
          html +=
            '<label><input type="radio" name="qa-station" value="' +
            safeId +
            '">' +
            safeName +
            " (ID: " +
            safeId +
            ")</label>";
        });
        _$("qa-stations").innerHTML = html;
        _$("qa-stations").style.display = "block";
        setMsg(
          "qa-otp-msg",
          "\u05d9\u05e9 \u05db\u05de\u05d4 \u05ea\u05d7\u05e0\u05d5\u05ea \u2014 \u05d1\u05d7\u05e8 \u05d5\u05dc\u05d7\u05e5 \u05e9\u05d5\u05d1 \u05e2\u05dc \u05d0\u05de\u05ea",
          "qa-info"
        );
      } else {
        setMsg("qa-otp-msg", d.detail || "\u05d0\u05d9\u05de\u05d5\u05ea \u05e0\u05db\u05e9\u05dc", "qa-err");
      }
    } catch (e) {
      setMsg("qa-otp-msg", "\u05e9\u05d2\u05d9\u05d0\u05ea \u05e8\u05e9\u05ea: " + e.message, "qa-err");
    }
    _$("qa-verify").disabled = false;
  }

  /* ── חיבור כפתורים (onclick) ── */
  document.addEventListener("click", function (e) {
    var id = e.target.id;
    if (id === "qa-key-btn") onSetAdminKey();
    else if (id === "qa-send") onRequestOTP();
    else if (id === "qa-verify") onVerifyOTP();
  });
})();
