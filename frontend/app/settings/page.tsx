import { SettingsScreen } from "@/components/settings-screen";
import { createClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

export default async function SettingsPage() {
  let supabase = { ok: false, detail: "Supabase env is missing." };
  if (process.env.NEXT_PUBLIC_SUPABASE_URL && process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY) {
    try {
      const client = await createClient();
      const { error, data } = await client.from("posts").select("id").limit(1);
      supabase = error
        ? { ok: false, detail: error.message }
        : { ok: true, detail: `${data?.length ?? 0} recent posts visible to the server key` };
    } catch (exc) {
      supabase = { ok: false, detail: exc instanceof Error ? exc.message : "Could not reach Supabase." };
    }
  }
  return <SettingsScreen supabase={supabase} />;
}
