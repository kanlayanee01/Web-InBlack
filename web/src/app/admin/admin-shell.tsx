"use client";

import Link from "next/link";
import {
  createContext,
  useContext,
  useEffect,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";

import { ThemeToggle } from "@/components/theme-toggle";
import { Field, Spinner } from "@/components/ui";
import { api, ApiError } from "@/lib/api";

export type Role = "viewer" | "support" | "finance" | "superadmin";
const RANK: Record<Role, number> = { viewer: 1, support: 2, finance: 3, superadmin: 4 };

interface Admin {
  email: string;
  role: Role;
  csrf_token: string;
}

const AdminContext = createContext<Admin | null>(null);

export function useAdmin(): Admin & { can: (minimum: Role) => boolean } {
  const admin = useContext(AdminContext);
  if (admin === null) throw new Error("useAdmin must be used inside AdminShell");
  return { ...admin, can: (minimum) => RANK[admin.role] >= RANK[minimum] };
}

export function AdminShell({ children }: { children: ReactNode }) {
  const [admin, setAdmin] = useState<Admin | null>(null);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    api<Admin>("/api/v1/admin/auth/me")
      .then(setAdmin)
      .catch(() => undefined)
      .finally(() => setChecking(false));
  }, []);

  if (checking) {
    return (
      <main className="container">
        <div className="skeleton" aria-busy="true" />
      </main>
    );
  }
  if (admin === null) return <Login onSignedIn={setAdmin} />;

  const canFinance = RANK[admin.role] >= RANK.finance;
  return (
    <AdminContext.Provider value={admin}>
      <header className="header">
        <div className="header-inner">
          <Link className="brand" href="/admin">
            InBlack Admin
          </Link>
          <nav className="nav" aria-label="เมนูผู้ดูแล">
            <Link href="/admin">แดชบอร์ด</Link>
            <Link href="/admin/orders">ออเดอร์</Link>
            {canFinance ? <Link href="/admin/reconciliation">กระทบยอด</Link> : null}
            {admin.role === "superadmin" ? <Link href="/admin/products">สินค้า</Link> : null}
            <ThemeToggle toDark="เปลี่ยนเป็นโหมดมืด" toLight="เปลี่ยนเป็นโหมดสว่าง" />
            <button
              className="btn btn-secondary"
              type="button"
              onClick={async () => {
                await api("/api/v1/admin/auth/logout", {
                  method: "POST",
                  headers: { "X-CSRF-Token": admin.csrf_token },
                });
                setAdmin(null);
              }}
            >
              ออกจากระบบ ({admin.role})
            </button>
          </nav>
        </div>
      </header>
      <main className="container">{children}</main>
    </AdminContext.Provider>
  );
}

function Login({ onSignedIn }: { onSignedIn: (admin: Admin) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onSignedIn(
        await api<Admin>("/api/v1/admin/auth/login", {
          method: "POST",
          json: { email: email.trim(), password },
        }),
      );
    } catch (failure) {
      setError(
        failure instanceof ApiError && failure.status === 401
          ? "อีเมลหรือรหัสผ่านไม่ถูกต้อง"
          : "เข้าสู่ระบบไม่สำเร็จ กรุณาลองใหม่",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="container" style={{ maxWidth: 420 }}>
      <form className="card stack" onSubmit={submit} noValidate>
        <h1>เข้าสู่ระบบผู้ดูแล</h1>
        <Field id="admin-email" label="อีเมล">
          {(describedBy) => (
            <input
              id="admin-email"
              className="input"
              type="email"
              autoComplete="username"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              aria-describedby={describedBy}
            />
          )}
        </Field>
        <Field id="admin-password" label="รหัสผ่าน" error={error}>
          {(describedBy, invalid) => (
            <input
              id="admin-password"
              className="input"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              aria-describedby={describedBy}
              aria-invalid={invalid}
            />
          )}
        </Field>
        <button className="btn" type="submit" disabled={busy}>
          {busy ? <Spinner /> : null} เข้าสู่ระบบ
        </button>
      </form>
    </main>
  );
}
