# env

Environment variable setup for ASK.

| File | Purpose | Committed |
| --- | --- | --- |
| `env/example.env` | Template listing every variable the app reads | yes |
| `../.env` (project root) | Your real values; Expo loads this file | no (gitignored) |

## Setup

1. Open the project-root `.env`.
2. Fill in the values listed in `env/example.env`.
3. Restart the dev server (`npx expo start --web --port 8081`). Expo reads `.env` at startup.

Expo only reads `.env` files from the project root, so real values cannot live inside this folder.

## Variables

| Name | Used by | Notes |
| --- | --- | --- |
| `EXPO_PUBLIC_SUPABASE_URL` | `src/lib/supabaseClient.ts` | Supabase project URL |
| `EXPO_PUBLIC_SUPABASE_ANON_KEY` | `src/lib/supabaseClient.ts` | Anon (public) key only |

## Rules

- Anything prefixed `EXPO_PUBLIC_` is bundled into the app and visible to users. Put only public values there.
- Never use a Supabase `service_role` key or any other secret in an `EXPO_PUBLIC_` variable.
- `.env` and `.env.*` are gitignored. Do not commit real keys or paste them into chat.
- When you add a new variable in code, add it to `env/example.env` and the table above.
