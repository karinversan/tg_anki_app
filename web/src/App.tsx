import { Route, Routes } from "react-router-dom";
import TopicsPage from "./pages/TopicsPage";
import TopicDetailPage from "./pages/TopicDetailPage";
import GenerationPage from "./pages/GenerationPage";
import DebugPanel from "./components/DebugPanel";
import { useTelegramAuth } from "./hooks/useTelegramAuth";

export default function App() {
  const { ready, error, allowStoredToken, continueWithStoredToken, debug } = useTelegramAuth();

  if (error) {
    return (
      <div className="centered">
        <div>
          <div>{error}</div>
          {allowStoredToken && (
            <button
              className="primary"
              onClick={continueWithStoredToken}
              style={{ marginTop: 12 }}
            >
              Continue with saved token
            </button>
          )}
          {debug && <DebugPanel />}
        </div>
      </div>
    );
  }

  if (!ready) {
    return (
      <div className="centered">
        <div>
          <div>Loading...</div>
          {debug && <DebugPanel />}
        </div>
      </div>
    );
  }

  return (
    <>
      <Routes>
        <Route path="/" element={<TopicsPage />} />
        <Route path="/topics/:topicId" element={<TopicDetailPage />} />
        <Route path="/topics/:topicId/generate" element={<GenerationPage />} />
      </Routes>
      {debug && <DebugPanel />}
    </>
  );
}
