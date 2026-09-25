"use server";

import { redirect } from "next/navigation";

import { ApiError, login } from "@/lib/api/server";
import { safeNext } from "@/lib/safe-redirect";
import { clearSession, setSession } from "@/lib/session";

export type LoginState = { error: string } | undefined;

const MESSAGES: Record<number, string> = {
  401: "Wrong username or password.",
  429: "Too many attempts. Wait a minute and try again.",
  503: "Sign-in is not set up on this server yet.",
};

export async function loginAction(_previous: LoginState, form: FormData): Promise<LoginState> {
  const username = String(form.get("username") ?? "").trim();
  const password = String(form.get("password") ?? "");
  const next = safeNext(String(form.get("next") ?? ""));
  if (!username || !password) return { error: "Enter your username and password." };

  let session;
  try {
    session = await login(username, password);
  } catch (error) {
    if (error instanceof ApiError) return { error: MESSAGES[error.status] ?? "Could not sign in. Try again." };
    return { error: "Could not reach the server. Try again in a moment." };
  }
  await setSession(session.token, session.expires_in);
  redirect(next);
}

export async function logoutAction(): Promise<void> {
  await clearSession();
  redirect("/login");
}
