import { useCallback, useEffect, useState } from "react";
import { createTopic, deleteTopic, listTopics, updateTopic, Topic } from "../api/client";
import { useNavigate } from "react-router-dom";
import { getWebApp } from "../telegram";
import { useAutoDismiss } from "../hooks/useAutoDismiss";
import { toErrorMessage } from "../utils/errors";
import { formatDate } from "../utils/format";

export default function TopicsPage() {
  const [topics, setTopics] = useState<Topic[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useAutoDismiss<string>(null);
  const [isAddOpen, setIsAddOpen] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<Topic | null>(null);
  const [menuTarget, setMenuTarget] = useState<Topic | null>(null);
  const [renameTarget, setRenameTarget] = useState<Topic | null>(null);
  const [renameTitle, setRenameTitle] = useState("");
  const [notice, setNotice] = useAutoDismiss<string>(null);
  const navigate = useNavigate();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listTopics();
      setTopics(data);
    } catch {
      setError("Failed to load topics");
    } finally {
      setLoading(false);
    }
  }, [setError]);

  useEffect(() => {
    load();
  }, [load]);

  const onAddTopic = async () => {
    if (!newTitle.trim()) return;
    try {
      const created = await createTopic(newTitle.trim());
      setTopics((current) => [created, ...current]);
      getWebApp()?.HapticFeedback?.impactOccurred("medium");
      setNewTitle("");
      setIsAddOpen(false);
    } catch {
      setError("Failed to create topic");
    }
  };

  const onDelete = async () => {
    if (!deleteTarget) return;
    try {
      await deleteTopic(deleteTarget.id);
      setTopics((current) => current.filter((t) => t.id !== deleteTarget.id));
      setDeleteTarget(null);
    } catch {
      setError("Failed to delete topic");
    }
  };

  const onShare = async (topic: Topic) => {
    const url = `${window.location.origin}/topics/${topic.id}`;
    await navigator.clipboard.writeText(url);
    setNotice("Ссылка на тему скопирована");
    setMenuTarget(null);
  };

  const openRename = (topic: Topic) => {
    setRenameTarget(topic);
    setRenameTitle(topic.title);
    setMenuTarget(null);
  };

  const onRename = async () => {
    if (!renameTarget || !renameTitle.trim()) return;
    try {
      const updated = await updateTopic(renameTarget.id, renameTitle.trim());
      setTopics((current) => current.map((t) => (t.id === updated.id ? updated : t)));
      setRenameTarget(null);
      setNotice("Тема обновлена");
    } catch (err) {
      setError(toErrorMessage(err, "Не удалось обновить тему"));
    }
  };

  if (loading) return <div className="centered">Loading...</div>;

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>Темы</h1>
          <p className="page-subtitle">Организуйте источники и создавайте Anki-колоды.</p>
        </div>
        <button className="primary" onClick={() => setIsAddOpen(true)}>
          + Создать тему
        </button>
      </header>
      {error && <div className="error">{error}</div>}
      {notice && <div className="notice">{notice}</div>}
      <div className="list">
        {topics.map((topic) => (
          <div key={topic.id} className="list-item">
            <div className="list-item-main">
              <div className="card-title-row">
                <h3 className="card-title truncate">{topic.title}</h3>
                <span className="pill">{topic.file_count} файлов</span>
              </div>
              <div className="list-meta">
                <span>{`Создана ${formatDate(topic.created_at)}`}</span>
                <span>{`Обновлена ${formatDate(topic.updated_at)}`}</span>
              </div>
            </div>
            <div className="list-actions">
              <button className="primary" onClick={() => navigate(`/topics/${topic.id}`)}>
                Открыть
              </button>
              <button className="icon-button" onClick={() => setMenuTarget(topic)} aria-label="Menu">
                ⋯
              </button>
            </div>
          </div>
        ))}
      </div>

      {isAddOpen && (
        <div className="modal-overlay">
          <div className="modal">
            <h3>Новая тема</h3>
            <label className="form">
              <span className="muted">Название темы</span>
              <input
                value={newTitle}
                onChange={(event) => setNewTitle(event.target.value)}
                placeholder="Например: Биология, глава 1"
              />
            </label>
            <div className="modal-actions">
              <button className="ghost" onClick={() => setIsAddOpen(false)}>
                Отмена
              </button>
              <button className="primary" onClick={onAddTopic}>
                Создать
              </button>
            </div>
          </div>
        </div>
      )}

      {deleteTarget && (
        <div className="modal-overlay">
          <div className="modal">
            <h3>Удалить тему</h3>
            <p className="muted">
              Удалить "{deleteTarget.title}" и все связанные файлы?
            </p>
            <div className="modal-actions">
              <button className="ghost" onClick={() => setDeleteTarget(null)}>
                Отмена
              </button>
              <button className="primary" onClick={onDelete}>
                Удалить
              </button>
            </div>
          </div>
        </div>
      )}

      {menuTarget && (
        <div className="modal-overlay">
          <div className="modal">
            <h3>Действия</h3>
            <div className="menu-list">
              <button className="ghost menu-item" onClick={() => navigate(`/topics/${menuTarget.id}`)}>
                Открыть
              </button>
              <button className="ghost menu-item" onClick={() => openRename(menuTarget)}>
                Переименовать
              </button>
              <button className="ghost menu-item" onClick={() => onShare(menuTarget)}>
                Поделиться
              </button>
              <button className="ghost menu-item" onClick={() => { setDeleteTarget(menuTarget); setMenuTarget(null); }}>
                Удалить
              </button>
            </div>
            <div className="modal-actions">
              <button className="ghost" onClick={() => setMenuTarget(null)}>Закрыть</button>
            </div>
          </div>
        </div>
      )}

      {renameTarget && (
        <div className="modal-overlay">
          <div className="modal">
            <h3>Переименовать</h3>
            <label className="form">
              <span className="muted">Новое название</span>
              <input
                value={renameTitle}
                onChange={(event) => setRenameTitle(event.target.value)}
              />
            </label>
            <div className="modal-actions">
              <button className="ghost" onClick={() => setRenameTarget(null)}>
                Отмена
              </button>
              <button className="primary" onClick={onRename}>
                Сохранить
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
