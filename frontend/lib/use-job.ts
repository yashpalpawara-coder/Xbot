"use client";

import { useEffect, useState } from "react";

import { request } from "@/lib/api";
import type { Job } from "@/lib/types";

export function useJob(id: string | null) {
  const [job, setJob] = useState<Job | null>(null);

  useEffect(() => {
    if (!id) {
      setJob(null);
      return undefined;
    }
    let stopped = false;

    async function tick() {
      try {
        const next = await request<Job>(`/api/jobs/${id}`);
        if (!stopped) setJob(next);
        return next.status;
      } catch {
        return "running";
      }
    }

    void tick();
    const timer = window.setInterval(async () => {
      const status = await tick();
      if (status === "done" || status === "error" || status === "stopped") {
        window.clearInterval(timer);
      }
    }, 1000);

    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [id]);

  return job;
}
