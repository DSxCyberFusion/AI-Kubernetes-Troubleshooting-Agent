import { createClient } from "@insforge/sdk";

let client: ReturnType<typeof createClient> | undefined;

export function getInsforgeClient() {
  if (client) {
    return client;
  }

  const baseUrl = process.env.NEXT_PUBLIC_INSFORGE_URL?.trim();
  const anonKey = process.env.NEXT_PUBLIC_INSFORGE_ANON_KEY?.trim();

  if (!baseUrl || !anonKey) {
    throw new Error(
      "Configure NEXT_PUBLIC_INSFORGE_URL and NEXT_PUBLIC_INSFORGE_ANON_KEY to use the dashboard.",
    );
  }

  client = createClient({ baseUrl, anonKey });
  return client;
}
