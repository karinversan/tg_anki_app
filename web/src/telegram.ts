type TelegramThemeParams = {
  bg_color?: string;
  text_color?: string;
  secondary_bg_color?: string;
  button_color?: string;
};

type TelegramWebApp = {
  initData?: string;
  themeParams?: TelegramThemeParams;
  ready?: () => void;
  expand?: () => void;
  onEvent?: (event: string, cb: () => void) => void;
  offEvent?: (event: string, cb: () => void) => void;
  HapticFeedback?: {
    impactOccurred?: (style: "light" | "medium" | "heavy" | "rigid" | "soft") => void;
  };
};

export const getWebApp = (): TelegramWebApp | undefined => {
  const typedWindow = window as Window & { Telegram?: { WebApp?: TelegramWebApp } };
  return typedWindow.Telegram?.WebApp;
};

export const initTelegram = () => {
  const setAppHeight = () => {
    const height = window.visualViewport?.height ?? window.innerHeight;
    document.documentElement.style.setProperty("--app-height", `${height}px`);
  };

  setAppHeight();
  window.addEventListener("resize", setAppHeight);
  window.visualViewport?.addEventListener("resize", setAppHeight);

  const webApp = getWebApp();
  if (webApp) {
    webApp.ready();
    webApp.expand();
    if (typeof webApp.onEvent === "function") {
      webApp.onEvent("viewportChanged", setAppHeight);
    }
  }

  return () => {
    window.removeEventListener("resize", setAppHeight);
    window.visualViewport?.removeEventListener("resize", setAppHeight);
    if (webApp && typeof webApp.offEvent === "function") {
      webApp.offEvent("viewportChanged", setAppHeight);
    }
  };
};
