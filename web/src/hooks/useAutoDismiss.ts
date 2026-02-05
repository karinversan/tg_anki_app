import { useEffect, useState } from "react";

export function useAutoDismiss<T>(initial: T | null, timeoutMs = 2500) {
  const [value, setValue] = useState<T | null>(initial);

  useEffect(() => {
    if (value === null) return;
    const timer = window.setTimeout(() => setValue(null), timeoutMs);
    return () => window.clearTimeout(timer);
  }, [value, timeoutMs]);

  return [value, setValue] as const;
}
