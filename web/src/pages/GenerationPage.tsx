import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  cancelJob,
  latestJob,
  retryJob,
  sendJobToTelegram,
  startJob,
  Job,
  listFiles,
  listTopics,
  downloadApkg
} from "../api/client";
import { getWebApp } from "../telegram";
import { useAutoDismiss } from "../hooks/useAutoDismiss";
import type { JobCreatePayload, JobDifficulty, JobMode } from "../api/types";

const MODES: { value: JobMode; label: string; description: string }[] = [
  {
    value: "merged",
    label: "Объединить",
    description: "Рекомендуется: общий контекст, меньше повторов"
  },
  {
    value: "per_file",
    label: "По файлам",
    description: "Отдельно по каждому файлу, затем объединение"
  },
  {
    value: "concat",
    label: "Один проход",
    description: "Быстро, но менее устойчиво на больших данных"
  }
];

const LLM_MODEL = "Ollama (local)";
const UI_MODELS = [
  {
    value: "qwen",
    label: "Qwen",
    description: "Лёгкая модель, ускорение на GPU/Metal"
  },
  {
    value: "trinity",
    label: "Trinity",
    description: "Альтернативная модель в том же локальном профиле"
  }
] as const;
type UiModel = (typeof UI_MODELS)[number]["value"];

const DIFFICULTIES: JobDifficulty[] = ["easy", "medium", "hard"];
const STAGE_LABELS: Record<string, string> = {
  queued: "в очереди",
  extracting: "извлекаем текст",
  chunking: "готовим фрагменты",
  generating: "генерируем вопросы",
  deduping: "убираем повторы",
  exporting: "формируем файл",
  done: "готово"
};

const metricToNumber = (value: unknown): number | null => {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return null;
};

const formatDurationShort = (seconds: number): string => {
  const total = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(total / 60);
  const secs = total % 60;
  if (minutes <= 0) return `${secs}с`;
  return `${minutes}м ${secs}с`;
};

export default function GenerationPage() {
  const { topicId } = useParams();
  const navigate = useNavigate();
  const [count, setCount] = useState(20);
  const [difficulty, setDifficulty] = useState<JobDifficulty>("medium");
  const [mode, setMode] = useState<JobMode>("merged");
  const [uiModel, setUiModel] = useState<UiModel>("qwen");
  const [avoidRepeats, setAvoidRepeats] = useState(true);
  const [includeAnswers, setIncludeAnswers] = useState(true);
  const [job, setJob] = useState<Job | null>(null);
  const [loading, setLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useAutoDismiss<string>(null);
  const [topicTitle, setTopicTitle] = useState<string>("");
  const [fileCount, setFileCount] = useState<number | null>(null);
  const pollTimerRef = useRef<number | null>(null);
  const clampCount = (value: number) => Math.min(200, Math.max(5, value));

  const stopPolling = () => {
    if (pollTimerRef.current) {
      window.clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  };

  const pollLatest = useCallback(async () => {
    if (!topicId) return;
    stopPolling();
    try {
      const data = await latestJob(topicId);
      setJob(data);
      if (data?.status === "queued" || data?.status === "running") {
        pollTimerRef.current = window.setTimeout(pollLatest, 2500);
      }
    } catch {
      setError("Failed to fetch job status");
    }
  }, [topicId]);

  useEffect(() => {
    pollLatest();
    return () => stopPolling();
  }, [pollLatest]);

  useEffect(() => {
    if (!topicId) return;
    const loadMeta = async () => {
      try {
        const [topics, files] = await Promise.all([listTopics(), listFiles(topicId)]);
        const topic = topics.find((item) => item.id === topicId);
        setTopicTitle(topic?.title || "");
        setFileCount(files.length);
      } catch {
        setFileCount(null);
      }
    };
    loadMeta();
  }, [topicId]);

  const onStart = async () => {
    if (!topicId) return;
    setError(null);
    setNotice(null);
    setLoading(true);
    try {
      const payload: JobCreatePayload = {
        mode,
        number_of_questions: count,
        difficulty,
        avoid_repeats: avoidRepeats,
        include_answers: includeAnswers
      };
      const newJob = await startJob(topicId, payload);
      setJob(newJob);
      if (newJob.status === "queued" || newJob.status === "running") {
        pollLatest();
      }
      getWebApp()?.HapticFeedback?.impactOccurred("heavy");
    } catch {
      setError("Failed to start generation");
    } finally {
      setLoading(false);
    }
  };

  const onCancel = async () => {
    if (!topicId || !job?.id) return;
    setError(null);
    setNotice(null);
    setActionLoading(true);
    try {
      const updated = await cancelJob(topicId, job.id);
      setJob(updated);
    } catch {
      setError("Failed to cancel generation");
    } finally {
      setActionLoading(false);
    }
  };

  const onRetry = async () => {
    if (!topicId || !job?.id) return;
    setError(null);
    setNotice(null);
    setActionLoading(true);
    try {
      const updated = await retryJob(topicId, job.id);
      setJob(updated);
      if (updated.status === "queued" || updated.status === "running") {
        pollLatest();
      }
    } catch {
      setError("Failed to retry generation");
    } finally {
      setActionLoading(false);
    }
  };

  const onSend = async () => {
    if (!topicId || !job?.id) return;
    setError(null);
    setNotice(null);
    setActionLoading(true);
    try {
      await sendJobToTelegram(topicId, job.id);
      setNotice("Sent to Telegram");
    } catch {
      setError("Failed to send file to Telegram");
    } finally {
      setActionLoading(false);
    }
  };

  const onDownload = async () => {
    if (!topicId || !job?.id) return;
    setError(null);
    setNotice(null);
    setActionLoading(true);
    try {
      await downloadApkg(topicId, job.id);
    } catch {
      setError("Не удалось скачать .apkg");
    } finally {
      setActionLoading(false);
    }
  };

  const canCancel = job?.status === "queued" || job?.status === "running";
  const canRetry = job?.status === "failed" || job?.status === "cancelled";
  const rawCount = job?.params_json?.number_of_questions;
  const cardsCount = typeof rawCount === "number" ? rawCount : count;
  const apkgName = job?.result_paths?.apkg?.split("/").pop();
  const metrics =
    job?.metrics_json && typeof job.metrics_json === "object"
      ? (job.metrics_json as Record<string, unknown>)
      : null;
  const stageSecondsRaw =
    metrics?.stage_seconds && typeof metrics.stage_seconds === "object"
      ? (metrics.stage_seconds as Record<string, unknown>)
      : null;
  const llmMetricsRaw =
    metrics?.agent_metrics &&
    typeof metrics.agent_metrics === "object" &&
    (metrics.agent_metrics as Record<string, unknown>).llm &&
    typeof (metrics.agent_metrics as Record<string, unknown>).llm === "object"
      ? ((metrics.agent_metrics as Record<string, unknown>).llm as Record<string, unknown>)
      : null;
  const metricsProvider = typeof metrics?.llm_provider === "string" ? metrics.llm_provider : null;
  const metricsModel = typeof metrics?.llm_model === "string" ? metrics.llm_model : null;
  const llmProvider = typeof llmMetricsRaw?.provider === "string" ? llmMetricsRaw.provider : null;
  const llmModel = typeof llmMetricsRaw?.model === "string" ? llmMetricsRaw.model : null;
  const chosenProvider = metricsProvider || llmProvider || "local";
  const chosenModel = metricsModel || llmModel || LLM_MODEL;
  const totalElapsedSec = metricToNumber(metrics?.total_elapsed_sec);
  const throughputQps = metricToNumber(metrics?.throughput_qps_end_to_end);
  const dedupeRemoved = metricToNumber(metrics?.dedupe_removed);
  const qualityScore = metricToNumber(metrics?.quality_score);
  const sourceCoverageRatio = metricToNumber(metrics?.source_coverage_ratio);
  const uniqueSourceCount = metricToNumber(metrics?.unique_source_count);
  const genStageSec = metricToNumber(stageSecondsRaw?.generating);
  const llmCalls = metricToNumber(llmMetricsRaw?.calls_total);
  const llmLatencyAvgSec = metricToNumber(llmMetricsRaw?.latency_avg_sec);
  const runtimeRaw =
    metrics?.runtime && typeof metrics.runtime === "object"
      ? (metrics.runtime as Record<string, unknown>)
      : null;
  const runtimeEtaSec = metricToNumber(runtimeRaw?.eta_seconds);
  const runtimeStageElapsedSec = metricToNumber(runtimeRaw?.stage_elapsed_sec);
  const runtimeUnitsDone = metricToNumber(runtimeRaw?.units_done);
  const runtimeUnitsTotal = metricToNumber(runtimeRaw?.units_total);
  const selectedUiModel = UI_MODELS.find((item) => item.value === uiModel) || UI_MODELS[0];
  const statusTitle =
    job?.status === "done"
      ? "Готово ✅"
      : job?.status === "failed"
      ? "Не удалось сгенерировать"
      : job?.status === "cancelled"
      ? "Отменено"
      : "Генерируем колоду…";
  const formatJobError = (message?: string | null) => {
    if (!message) return "Ошибка генерации";
    const lowered = message.toLowerCase();
    if (
      lowered.includes("embed_content") ||
      lowered.includes("embedding-001") ||
      lowered.includes("embed_content") ||
      lowered.includes("embedding")
    ) {
      return "Квота эмбеддингов исчерпана. Запусти повтор или отключи embeddings.";
    }
    if (lowered.includes("network connection lost") || lowered.includes("502") || lowered.includes("gateway")) {
      return "Сетевая ошибка у провайдера LLM. Подожди минуту и повтори.";
    }
    if (lowered.includes("insufficient credits") || lowered.includes("payment required") || lowered.includes(" 402")) {
      return "Недостаточно кредитов у провайдера. Пополни баланс или смени ключ.";
    }
    if (lowered.includes("rate limit") || lowered.includes("429")) {
      return "Лимит запросов у провайдера. Подожди и повтори.";
    }
    if (lowered.includes("provider returned error") || lowered.includes("upstream")) {
      return "Ошибка у провайдера модели. Попробуй повторить позже или сменить модель.";
    }
    if (lowered.includes("generate_content") || lowered.includes("generate") || lowered.includes("quota")) {
      return "Квота генерации у провайдера исчерпана. Подожди и повтори, либо смени модель.";
    }
    return message;
  };

  let statusSubtitle = "";
  if (job?.status === "queued") {
    statusSubtitle = "В очереди";
  } else if (job?.status === "running") {
    const base = `Этап: ${STAGE_LABELS[job.stage] || job.stage}`;
    if (job.stage === "generating") {
      const parts = [base];
      if (runtimeUnitsDone !== null && runtimeUnitsTotal !== null && runtimeUnitsTotal > 0) {
        parts.push(`Прогресс: ${Math.min(runtimeUnitsDone, runtimeUnitsTotal)}/${runtimeUnitsTotal}`);
      }
      if (runtimeEtaSec !== null) {
        parts.push(`Оценка: ~${formatDurationShort(runtimeEtaSec)}`);
      }
      if (runtimeStageElapsedSec !== null) {
        parts.push(`Прошло: ${formatDurationShort(runtimeStageElapsedSec)}`);
      }
      statusSubtitle = parts.join(" · ");
    } else {
      statusSubtitle = base;
    }
  } else if (job?.status === "failed") {
    statusSubtitle = formatJobError(job?.error_message);
  } else if (job?.status === "cancelled") {
    statusSubtitle = "Генерация остановлена";
  }

  return (
    <div className="page">
      <header className="page-header">
        <div className="page-header-left">
          <button className="primary" onClick={() => navigate(`/topics/${topicId}`)}>
            Назад
          </button>
          <div>
            <h1>Параметры генерации</h1>
            <p className="page-subtitle">Настройте параметры и запустите генерацию.</p>
          </div>
        </div>
      </header>
      {error && <div className="error">{error}</div>}
      {notice && <div className="notice">{notice}</div>}
      <div className="panel form">
        <div className="field-row">
          <div>
            <div className="field-label">Количество вопросов</div>
            <div className="field-subtitle">5–200 карточек</div>
          </div>
          <div className="stepper">
            <button
              className="ghost"
              onClick={() => setCount(clampCount(count - 5))}
              type="button"
            >
              −
            </button>
            <input
              type="number"
              min={5}
              max={200}
              value={count}
              onChange={(e) => setCount(clampCount(Number(e.target.value) || 5))}
            />
            <button
              className="ghost"
              onClick={() => setCount(clampCount(count + 5))}
              type="button"
            >
              +
            </button>
          </div>
        </div>

        <div>
          <div className="field-label">Сложность</div>
          <div className="segmented">
            {DIFFICULTIES.map((value) => (
              <button
                key={value}
                className={difficulty === value ? "segment active" : "segment"}
                onClick={() => setDifficulty(value)}
                type="button"
              >
                {value === "easy" ? "Легко" : value === "medium" ? "Средне" : "Сложно"}
              </button>
            ))}
          </div>
        </div>

        <div>
          <div className="field-label">Режим генерации</div>
          <div className="segmented segmented-vertical">
            {MODES.map((item) => (
              <button
                key={item.value}
                className={mode === item.value ? "segment active" : "segment"}
                onClick={() => setMode(item.value)}
                type="button"
              >
                <div className="segment-title">{item.label}</div>
                <div className="segment-subtitle">{item.description}</div>
              </button>
            ))}
          </div>
        </div>

        <div>
          <div className="field-label">Модель генерации</div>
          <select value={uiModel} onChange={(e) => setUiModel(e.target.value as UiModel)}>
            {UI_MODELS.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
          <div className="field-subtitle">Локальная через Ollama: {selectedUiModel.description}</div>
          <div className="field-subtitle">Пока выбор интерфейсный и не влияет на генерацию.</div>
        </div>

        <div className="switch-row">
          <div>
            <div className="field-label">Без повторов</div>
            <div className="field-subtitle">Убираем дубликаты</div>
          </div>
          <label className="switch">
            <input type="checkbox" checked={avoidRepeats} onChange={() => setAvoidRepeats(!avoidRepeats)} />
            <span />
          </label>
        </div>

        <div className="switch-row">
          <div>
            <div className="field-label">Добавлять ответы</div>
            <div className="field-subtitle">Ответы внутри карточек</div>
          </div>
          <label className="switch">
            <input type="checkbox" checked={includeAnswers} onChange={() => setIncludeAnswers(!includeAnswers)} />
            <span />
          </label>
        </div>

        <button className="primary" onClick={onStart} disabled={loading}>
          {loading ? "Запуск..." : "Старт генерации"}
        </button>
      </div>

      {job && (
        <div className="panel status">
          <h2>{statusTitle}</h2>
          {job.status !== "done" && (
            <>
              {statusSubtitle && <p className="muted">{statusSubtitle}</p>}
              <div className="progress">
                <span style={{ width: `${job.progress}%` }} />
              </div>
            </>
          )}
          {job.status === "done" && (
            <div className="summary">
              <div className="summary-row">
                <span className="muted">Тема</span>
                <span>{topicTitle || "Без названия"}</span>
              </div>
              <div className="summary-row">
                <span className="muted">Карточек</span>
                <span>{cardsCount}</span>
              </div>
              <div className="summary-row">
                <span className="muted">Файлов</span>
                <span>{fileCount ?? "—"}</span>
              </div>
              <div className="summary-row">
                <span className="muted">Модель</span>
                <span>{chosenModel}</span>
              </div>
              <div className="summary-row">
                <span className="muted">Провайдер</span>
                <span>{chosenProvider}</span>
              </div>
              {totalElapsedSec !== null && (
                <div className="summary-row">
                  <span className="muted">Время (E2E)</span>
                  <span>{totalElapsedSec.toFixed(2)} сек</span>
                </div>
              )}
              {genStageSec !== null && (
                <div className="summary-row">
                  <span className="muted">Генерация</span>
                  <span>{genStageSec.toFixed(2)} сек</span>
                </div>
              )}
              {throughputQps !== null && (
                <div className="summary-row">
                  <span className="muted">Скорость</span>
                  <span>{throughputQps.toFixed(2)} вопросов/сек</span>
                </div>
              )}
              {qualityScore !== null && (
                <div className="summary-row">
                  <span className="muted">Quality score</span>
                  <span>{qualityScore.toFixed(1)} / 100</span>
                </div>
              )}
              {sourceCoverageRatio !== null && (
                <div className="summary-row">
                  <span className="muted">Source coverage</span>
                  <span>{(sourceCoverageRatio * 100).toFixed(0)}%</span>
                </div>
              )}
              {uniqueSourceCount !== null && (
                <div className="summary-row">
                  <span className="muted">Уникальных источников</span>
                  <span>{Math.max(0, Math.round(uniqueSourceCount))}</span>
                </div>
              )}
              {llmCalls !== null && (
                <div className="summary-row">
                  <span className="muted">LLM вызовов</span>
                  <span>{Math.round(llmCalls)}</span>
                </div>
              )}
              {llmLatencyAvgSec !== null && (
                <div className="summary-row">
                  <span className="muted">LLM avg latency</span>
                  <span>{(llmLatencyAvgSec * 1000).toFixed(0)} мс</span>
                </div>
              )}
              {dedupeRemoved !== null && (
                <div className="summary-row">
                  <span className="muted">Удалено дублей</span>
                  <span>{Math.max(0, Math.round(dedupeRemoved))}</span>
                </div>
              )}
              {apkgName && (
                <div className="summary-row">
                  <span className="muted">Файл</span>
                  <span className="truncate">{apkgName}</span>
                </div>
              )}
            </div>
          )}
          <div className="meta-row">
            <span className="muted">Job ID: {job.id}</span>
            <button
              className="ghost"
              onClick={() => navigator.clipboard.writeText(job.id)}
              type="button"
            >
              Скопировать
            </button>
          </div>
          {(job.status === "failed" || job.status === "cancelled") && (
            <p className="error">{statusSubtitle}</p>
          )}
          {job.status === "done" && !job.result_paths?.apkg && (
            <p className="error">.apkg не найден. Запустите генерацию снова.</p>
          )}
          <div className="status-actions">
            {canCancel && (
              <button className="ghost" onClick={onCancel} disabled={actionLoading}>
                {actionLoading ? "Отмена..." : "Отменить"}
              </button>
            )}
            {canRetry && (
              <button className="primary" onClick={onRetry} disabled={actionLoading}>
                {actionLoading ? "Повтор..." : "Повторить"}
              </button>
            )}
          </div>
          {job.status === "done" && job.id && job.result_paths?.apkg && (
            <div className="downloads">
              <button className="primary" onClick={onSend} disabled={actionLoading}>
                {actionLoading ? "Отправка..." : "Отправить в Telegram"}
              </button>
              <button
                className="ghost"
                onClick={onDownload}
                type="button"
              >
                Скачать .apkg
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
