import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { deleteFile, listFiles, uploadFiles, FileRecord } from "../api/client";
import { useAutoDismiss } from "../hooks/useAutoDismiss";
import { toErrorMessage } from "../utils/errors";
import { formatBytes, formatDate } from "../utils/format";

export default function TopicDetailPage() {
  const { topicId } = useParams();
  const navigate = useNavigate();
  const [files, setFiles] = useState<FileRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useAutoDismiss<string>(null);
  const [deleteTarget, setDeleteTarget] = useState<FileRecord | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    if (!topicId) return;
    setLoading(true);
    try {
      const data = await listFiles(topicId);
      setFiles(data);
    } catch {
      setError("Failed to load files");
    } finally {
      setLoading(false);
    }
  }, [topicId, setError]);

  useEffect(() => {
    load();
  }, [load]);

  const handleUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    if (!topicId || !event.target.files) return;
    setError(null);
    try {
      await uploadFiles(topicId, Array.from(event.target.files));
      event.target.value = "";
      await load();
    } catch (err) {
      setError(toErrorMessage(err, "Upload failed"));
    }
  };

  const handleDelete = async () => {
    if (!topicId) return;
    if (!deleteTarget) return;
    try {
      await deleteFile(topicId, deleteTarget.id);
      setFiles((current) => current.filter((file) => file.id !== deleteTarget.id));
      setDeleteTarget(null);
    } catch {
      setError("Failed to delete file");
    }
  };

  if (loading) return <div className="centered">Loading...</div>;

  const openFilePicker = () => {
    fileInputRef.current?.click();
  };

  return (
    <div className="page">
      <header className="page-header">
        <div className="page-header-left">
          <button className="primary" onClick={() => navigate("/")}>Назад</button>
          <div>
            <h1>Файлы темы</h1>
            <p className="page-subtitle">Загружайте источники для качественных карточек.</p>
          </div>
        </div>
        <span className="pill">Файлы</span>
      </header>
      {error && <div className="error">{error}</div>}
      <div className="upload-panel">
        <div>
          <div className="field-label">Загрузка файлов</div>
          <div className="upload-note">Поддерживаются PDF, TXT, MD, DOCX</div>
        </div>
        <button className="ghost" onClick={openFilePicker}>
          Загрузить файлы
        </button>
        <input
          ref={fileInputRef}
          className="file-input"
          id="file-input"
          type="file"
          multiple
          accept=".pdf,.txt,.md,.docx"
          onChange={handleUpload}
        />
      </div>

      {files.length === 0 ? (
        <div className="empty-state">
          Файлы не загружены — добавьте PDF, чтобы начать.
        </div>
      ) : (
        <div className="list">
          {files.map((file) => (
            <div key={file.id} className="list-item file-item">
              <div className="file-icon">
                {(file.original_filename.split(".").pop() || "FILE").slice(0, 4).toUpperCase()}
              </div>
              <div className="list-item-main">
                <h3 className="card-title truncate">{file.original_filename}</h3>
                <div className="list-meta">
                  <span>{formatBytes(file.size_bytes)}</span>
                  <span>{`Загружен ${formatDate(file.created_at)}`}</span>
                </div>
              </div>
              <div className="list-actions">
                <button className="icon-button" onClick={() => setDeleteTarget(file)} aria-label="Delete file">
                  ✕
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="sticky-cta">
        <button className="primary" onClick={() => navigate(`/topics/${topicId}/generate`)}>
          Сгенерировать вопросы
        </button>
      </div>

      {deleteTarget && (
        <div className="modal-overlay">
          <div className="modal">
            <h3>Удалить файл</h3>
            <p className="muted">
              Удалить "{deleteTarget.original_filename}"?
            </p>
            <div className="modal-actions">
              <button className="ghost" onClick={() => setDeleteTarget(null)}>
                Отмена
              </button>
              <button className="primary" onClick={handleDelete}>
                Удалить
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
