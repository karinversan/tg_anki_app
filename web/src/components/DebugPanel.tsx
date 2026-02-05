import { getWebApp } from "../telegram";

export default function DebugPanel() {
  const webApp = getWebApp();
  const token = typeof window !== "undefined" ? window.localStorage.getItem("tg_anki_token") : "";
  const initData = webApp?.initData || "";

  const copy = async (value: string) => {
    if (!value || !navigator.clipboard) return;
    await navigator.clipboard.writeText(value);
  };

  return (
    <div style={{ marginTop: 16, fontSize: 12, opacity: 0.8 }}>
      <div>debug=1</div>
      <div>initData length: {initData.length || 0}</div>
      <div>token length: {token?.length || 0}</div>
      <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
        <button className="ghost" onClick={() => copy(initData)}>
          Copy initData
        </button>
        <button className="ghost" onClick={() => copy(token || "")}>
          Copy token
        </button>
      </div>
    </div>
  );
}
