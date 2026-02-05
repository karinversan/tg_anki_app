import { useEffect, useMemo, useState } from "react";
import { authTelegram } from "../api/client";
import { getWebApp, initTelegram } from "../telegram";

export function useTelegramAuth() {
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [allowStoredToken, setAllowStoredToken] = useState(false);

  const debug = useMemo(() => {
    if (typeof window === "undefined") return false;
    return new URLSearchParams(window.location.search).get("debug") === "1";
  }, []);

  const storedToken = useMemo(() => {
    if (typeof window === "undefined") return null;
    return window.localStorage.getItem("tg_anki_token");
  }, []);

  useEffect(() => {
    const cleanupTelegram = initTelegram();
    const webApp = getWebApp();
    if (!webApp) {
      setError("Open this Mini App from Telegram (WebApp context missing).");
      if (storedToken) setAllowStoredToken(true);
      return () => cleanupTelegram?.();
    }
    if (webApp?.themeParams) {
      const root = document.documentElement;
      root.style.setProperty("--tg-bg", webApp.themeParams.bg_color || "#f8f6f2");
      root.style.setProperty("--tg-text", webApp.themeParams.text_color || "#1d1b16");
      root.style.setProperty("--tg-secondary-bg", webApp.themeParams.secondary_bg_color || "#ffffff");
      root.style.setProperty("--tg-accent", webApp.themeParams.button_color || "#2f7df6");
    }
    const initData = webApp?.initData || "";
    if (!initData) {
      setError("Missing Telegram initData. Open this app from the bot's WebApp button.");
      if (storedToken) setAllowStoredToken(true);
      return () => cleanupTelegram?.();
    }
    authTelegram(initData)
      .then(() => setReady(true))
      .catch((err) => {
        console.error(err);
        setError(`Authentication failed. ${err?.message || ""}`.trim());
      });

    return () => cleanupTelegram?.();
  }, [storedToken]);

  const continueWithStoredToken = () => {
    setError(null);
    setReady(true);
  };

  return { ready, error, allowStoredToken, continueWithStoredToken, debug };
}
