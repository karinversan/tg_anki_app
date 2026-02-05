export type Topic = {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  file_count: number;
};

export type FileRecord = {
  id: string;
  original_filename: string;
  mime_type: string;
  size_bytes: number;
  created_at: string;
};

export type Job = {
  id: string;
  status: string;
  progress: number;
  stage: string;
  params_json?: Record<string, unknown>;
  result_paths?: Record<string, string> | null;
  error_message?: string | null;
};

export type JobMode = "merged" | "per_file" | "concat";
export type JobDifficulty = "easy" | "medium" | "hard";

export type JobCreatePayload = {
  mode: JobMode;
  number_of_questions: number;
  difficulty: JobDifficulty;
  avoid_repeats?: boolean;
  include_answers?: boolean;
};
