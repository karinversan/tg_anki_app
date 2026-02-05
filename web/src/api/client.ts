import { request, setToken, getToken, buildApiUrl } from "./http";
import type { FileRecord, Job, JobCreatePayload, JobDifficulty, JobMode, Topic } from "./types";

export type { FileRecord, Job, JobCreatePayload, JobDifficulty, JobMode, Topic };

export async function authTelegram(initData: string) {
  const data = await request<{ access_token: string }>("/auth/telegram", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ init_data: initData }),
    fallbackError: "Auth failed"
  });
  setToken(data.access_token);
  return data;
}

export function listTopics(): Promise<Topic[]> {
  return request("/topics/", { fallbackError: "Failed to fetch topics" });
}

export function createTopic(title: string): Promise<Topic> {
  return request("/topics/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
    fallbackError: "Failed to create topic"
  });
}

export function deleteTopic(topicId: string): Promise<void> {
  return request(`/topics/${topicId}`, {
    method: "DELETE",
    fallbackError: "Failed to delete topic"
  });
}

export function updateTopic(topicId: string, title: string): Promise<Topic> {
  return request(`/topics/${topicId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
    fallbackError: "Failed to update topic"
  });
}

export function listFiles(topicId: string): Promise<FileRecord[]> {
  return request(`/topics/${topicId}/files/`, { fallbackError: "Failed to fetch files" });
}

export async function uploadFiles(topicId: string, files: File[]): Promise<void> {
  for (const file of files) {
    const form = new FormData();
    form.append("file", file);
    await request(`/topics/${topicId}/files/`, {
      method: "POST",
      body: form
    });
  }
}

export function deleteFile(topicId: string, fileId: string): Promise<void> {
  return request(`/topics/${topicId}/files/${fileId}`, {
    method: "DELETE",
    fallbackError: "Failed to delete file"
  });
}

export function startJob(topicId: string, payload: JobCreatePayload): Promise<Job> {
  return request(`/topics/${topicId}/jobs/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    fallbackError: "Failed to start generation"
  });
}

export function latestJob(topicId: string): Promise<Job | null> {
  return request(`/topics/${topicId}/jobs/latest`, { fallbackError: "Failed to fetch job" });
}

export function cancelJob(topicId: string, jobId: string): Promise<Job> {
  return request(`/topics/${topicId}/jobs/${jobId}/cancel`, {
    method: "POST",
    fallbackError: "Failed to cancel job"
  });
}

export function retryJob(topicId: string, jobId: string): Promise<Job> {
  return request(`/topics/${topicId}/jobs/${jobId}/retry`, {
    method: "POST",
    fallbackError: "Failed to retry job"
  });
}

export function sendJobToTelegram(topicId: string, jobId: string): Promise<void> {
  return request(`/topics/${topicId}/jobs/${jobId}/send`, {
    method: "POST",
    fallbackError: "Failed to send file"
  });
}

export async function downloadApkg(topicId: string, jobId: string): Promise<void> {
  const res = await fetch(buildApiUrl(`/topics/${topicId}/jobs/${jobId}/download/apkg`), {
    method: "GET",
    headers: getToken() ? { Authorization: `Bearer ${getToken()}` } : {}
  });
  if (!res.ok) throw new Error("Failed to download");
  const blob = await res.blob();
  const url = window.URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "anki_cards.apkg";
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(url);
}

export const downloadUrl = (topicId: string, jobId: string, format: string) =>
  buildApiUrl(`/topics/${topicId}/jobs/${jobId}/download/${format}`);
