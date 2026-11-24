(function () {
  "use strict";

  var LINKS = {
    macArm: "https://github.com/shaurya202/pith/releases/latest/download/Pith-macOS-arm64.dmg",
    macIntel: "https://github.com/shaurya202/pith/releases/latest/download/Pith-macOS-x86_64.dmg",
    win: "https://github.com/shaurya202/pith/releases/latest/download/Pith-Windows-x64.zip"
  };

  function detect() {
    var ua = navigator.userAgent || "";
    var platform = (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || "";
    var touches = navigator.maxTouchPoints || 0;
    var isIOS = /iPhone|iPad|iPod/.test(ua) || (platform === "MacIntel" && touches > 1);
    var isAndroid = /Android/.test(ua);
    var isMobile = isIOS || isAndroid;
    var isMac = !isIOS && (/Mac/i.test(platform) || /Mac OS X/.test(ua));
    var isWin = /Win/i.test(platform) || /Windows/.test(ua);
    if (isMobile) return "all";
    if (isMac) return "mac";
    if (isWin) return "windows";
    return "all";
  }

  var os = detect();
  var root = document.getElementById("download");
  var primary = document.getElementById("dl-primary");
  if (!root || !primary) return;
  root.setAttribute("data-os", os);
  if (os === "mac") {
    primary.href = LINKS.macArm;
    primary.textContent = "Download for Mac";
  } else if (os === "windows") {
    primary.href = LINKS.win;
    primary.textContent = "Download for Windows";
  }
})();
