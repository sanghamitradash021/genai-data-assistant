# Frontend Standards

> Conventions and patterns for the Entegris frontend (`@entegris/frontend`).
> Every contributor must follow this document. Update it when patterns evolve.

---

## Table of Contents

1. [Project Structure](#1-project-structure)
2. [File Naming](#2-file-naming)
3. [TypeScript](#3-typescript)
4. [Components](#4-components)
5. [Pages](#5-pages)
6. [Hooks](#6-hooks)
7. [State Management](#7-state-management)
8. [API & Services](#8-api--services)
9. [Routing](#9-routing)
10. [Styling](#10-styling)
11. [Imports](#11-imports)
12. [Sub-component Props](#12-sub-component-props)
13. [Page Sub-components](#13-page-sub-components)
14. [Hook-local Types](#14-hook-local-types)
15. [Functions Inside Hooks](#15-functions-inside-hooks)
16. [Error Handling in Hooks](#16-error-handling-in-hooks)
17. [Constants Format](#17-constants-format)
18. [Role Guards & Routing](#18-role-guards--routing)
19. [shadcn/ui](#19-shadcnui)
20. [UI Text Constants](#20-ui-text-constants)
21. [Accessibility (aria labels)](#21-accessibility-aria-labels)
22. [Assets (images & icons)](#22-assets-images--icons)
23. [Types Location](#23-types-location)
24. [Feature Development Checklist](#24-feature-development-checklist)
25. [Testing](#25-testing)

---

## 1. Project Structure

```
src/
├── components/
│   └── ui/               # shadcn/ui components only — do not edit manually
├── hooks/                # custom React hooks
├── layouts/              # route layout wrappers
├── lib/                  # third-party config (axios instance, utils)
├── pages/                # one folder per route group
│   ├── auth/
│   └── ...
├── services/             # API call functions (one file per domain)
├── store/                # Zustand stores (one file per domain)
└── types/                # shared TypeScript types (one file per domain)
|__constants/             # shared constants (one file per domain)
```

---

## 2. File Naming

- Use **plain `name.ts` / `name.tsx`** — no compound extensions.
- Do **not** use `name.store.ts`, `name.types.ts`, `name.service.ts`, `name.hook.ts`.
- Use **kebab-case** for all file and folder names.

```
✅ src/store/auth.ts
✅ src/types/auth.ts
✅ src/hooks/useLogin.ts
✅ src/pages/auth/login.tsx
✅ src/services/auth.ts
✅ src/constants/auth.ts

❌ src/store/auth.store.ts
❌ src/types/auth.types.ts
❌ src/services/auth.service.ts
```

---

## 3. TypeScript

- **Strict mode is on** — no `any`, no implicit returns, no unused variables.
- Use `interface` for object shapes, `type` for unions and aliases.
- All shared types live in `src/types/` — never define types inside hooks, services, or components.
- Import types with `import type { ... }`.
- Keep generics explicit and narrow — avoid `<T = any>`.

```ts
// ✅
import type { LoginForm } from '@/types/auth'

// ❌
const submit = async (data: any) => { ... }
```

---

## 4. Components

- Use **function declarations** (not arrow functions) for components.
- One component per file. File name matches the component name in kebab-case.
- **Always use shadcn/ui components** when one exists for the use case — do not build raw HTML equivalents.
- Only create a custom component if shadcn does not cover the need.
- Page-specific sub-components (e.g. a logo) may live in the page file if small and not reused.

```tsx
// ✅ named function
export default function LoginPage() { ... }

// ❌ arrow function export
export const LoginPage = () => { ... }
```

---

## 5. Pages

- One folder per route group under `src/pages/`.
- Pages are **pure UI** — no API calls, no store logic inside them.
- All side-effects and business logic go into a custom hook consumed by the page.

```tsx
// ✅ page delegates everything to a hook
export default function LoginPage() {
  const { email, handleSubmit, ... } = useLogin()
  return <form onSubmit={handleSubmit}>...</form>
}

// ❌ page calls the API directly
export default function LoginPage() {
  async function handleSubmit() {
    const res = await api.post('/auth/login', ...)
  }
}
```

---

## 6. Hooks

- Prefix with `use` — e.g. `useLogin`, `useProfile`.
- One hook per file under `src/hooks/`.
- A hook owns: form state, loading/error state, store writes, API calls, and navigation.
- Return a flat object — not an array — so callers can destructure by name.

```ts
// ✅ flat object return
return { email, isLoading, error, handleSubmit, setField }

// ❌ array return (hard to read at the call site)
return [email, isLoading, handleSubmit]
```

---

## 7. State Management

- Use **Zustand** for all global/shared state.
- One store per domain under `src/store/` (e.g. `auth.ts`, `ui.ts`).
- Persist sensitive or session-critical state with `zustand/middleware` `persist`.
- Never read from or write to a store directly inside a page — do it inside a hook.
- Keep selectors granular to avoid unnecessary re-renders.

```ts
// ✅ granular selector
const token = useAuthStore((s) => s.token)

// ❌ subscribing to the whole store
const store = useAuthStore()
```

---

## 8. API & Services

### Axios Instance

The shared axios instance lives at `src/lib/axios.ts`.
It handles:
- Base URL from `VITE_API_BASE_URL`
- `Authorization: Bearer <token>` injection
- Automatic 401 redirect to `/login`
- Normalized `ApiError` shape on failures

**Never create a second axios instance.** Always import from `@/lib/axios`.

### Services

- One file per domain under `src/services/` (e.g. `auth.ts`, `users.ts`).
- Services are plain async functions — no hooks, no state.
- When mocking, keep the real API function alongside the mock and swap via a single export.

```ts
// ✅ easy swap when backend is ready
export const login = loginMock   // → change to loginApi
```

### Error Handling

All API errors are typed as `ApiError` from `src/types/api.ts`.
Catch errors in hooks, not in services.

```ts
// ✅ catch in hook
try {
  const result = await login(credentials)
} catch (err) {
  const apiError = err as ApiError
  setError(apiError.message)
}
```

---

## 9. Routing

- Use **`createBrowserRouter`** (React Router v7 Data Mode) — not `<Routes>/<Route>` JSX.
- All routes are defined in `src/router.tsx` — the single source of truth.
- `src/main.tsx` only renders `<RouterProvider router={router} />`.
- Protected routes are wrapped in `AuthLayout` which checks the auth store.
- Public routes (login, forgot password) are siblings to `AuthLayout`.

```ts
// router.tsx — adding a new protected route
{
  Component: AuthLayout,
  children: [
    { path: '/', Component: HomePage },
    { path: '/dashboard', Component: DashboardPage },  // ← add here
  ],
}
```

---

## 10. Styling

- **Tailwind CSS v4** utility classes only — no inline `style` props, no plain CSS files (except `index.css`).
- **shadcn/ui tokens** for all colors — use `bg-primary`, `text-muted-foreground`, `border-border`, etc.
- Never hard-code hex or RGB color values in component files. Update `index.css` CSS variables instead.
- Use `cn()` from `@/lib/utils` to merge conditional classes.
- Dark mode is supported via the `.dark` class — keep tokens consistent.

```tsx
// ✅
<div className={cn('rounded-lg bg-card', isActive && 'ring-2 ring-primary')} />

// ❌
<div style={{ backgroundColor: '#1e3a6e' }} />
```

---

## 11. Imports

- Always use the `@/` path alias — no relative paths that climb directories.
- Group imports in this order, separated by a blank line:
  1. React / framework
  2. Third-party libraries
  3. Internal — components
  4. Internal — hooks / store / services
  5. Internal — types (`import type`)

```ts
// ✅
import { useState } from 'react'
import { useNavigate } from 'react-router'

import { Button } from '@/components/ui/button'

import { useLogin } from '@/hooks/useLogin'

import type { LoginForm } from '@/types/auth'
```

---

## 12. Sub-component Props

- Page-specific sub-components use a **local, exported `interface Props`** — use `ComponentNameProps` on respective types, never `React.FC<Props>`.
- Destructure props directly in the function signature.

```tsx
// ✅
interface Props {
  config: EtchConfig
  isGenerating: boolean
  onSubmit: (e: React.FormEvent) => Promise<void>
}

export default function ConfigForm({ config, isGenerating, onSubmit }: Props) { ... }

// ❌
export interface ConfigFormProps { ... }
const ConfigForm: React.FC<ConfigFormProps> = (props) => { ... }
```

---

## 13. Page Sub-components

- Page-specific sub-components live in **the same page folder** as `index.tsx`, each in its own file.
- The page `index.tsx` imports them via `@/pages/<domain>/<component-name>`.
- Sub-components are pure UI — they receive everything via props; no hook calls, no store reads inside them.

```
src/pages/qr-gen/
├── index.tsx           ← page, consumes useQrGen()
├── config-form.tsx     ← sub-component
├── live-preview.tsx    ← sub-component
├── generated-codes-tab.tsx
└── success-modal.tsx
```

---

## 14. Hook-local Types

- Types that are **only used inside one hook file** stay in that file — do not move them to `src/types/`.
- Only types shared across multiple files belong in `src/types/`.

```ts
// ✅ — ActiveTab is only used in useQrGen, keep it local
type ActiveTab = 'generated' | 'history'

export function useQrGen() { ... }
```

---

## 15. Functions Inside Hooks

- All helper and handler functions inside hooks use **`function` declarations**, not `const fn = () => {}`.
- Derived/computed values (not functions) use `const`.

```ts
// ✅
function handleGenerate(e: React.FormEvent) { ... }
function setConfigField<K extends keyof EtchConfig>(key: K, value: EtchConfig[K]) { ... }

const filteredBatches = batches.filter(...)  // ✅ derived value → const

// ❌
const handleGenerate = async (e: React.FormEvent) => { ... }
```

---

## 16. Error Handling in Hooks

- Always use `isAxiosError(err)` from `axios` to discriminate network errors.
- Cast `err.response?.data` inline — do not create a typed wrapper for the raw response body.
- Use `showErrorToast` / `showSuccessToast` from `@/lib/toast` for user feedback — never call `toast.*` directly.

```ts
// ✅
import { isAxiosError } from 'axios'
import { showErrorToast } from '@/lib/toast'

} catch (err) {
  if (isAxiosError(err)) {
    showErrorToast('Failed', (err.response?.data as { error?: string })?.error)
  } else {
    showErrorToast('Failed')
  }
}
```

---

## 17. Constants Format

- Dropdown option lists are `{ value: string; label: string }[]` objects.
- Constants files re-export shared values from `@entegris/shared` when applicable; add frontend-only constants in the same file.

```ts
// ✅ src/constants/qr-gen.ts
export { VENDOR_CODES, TRAY_CODES } from '@entegris/shared'
export const MATERIAL_OPTIONS = ENTEGRIS_MATERIALS.map((m) => ({ value: m, label: m }))
```

---

## 18. Role Guards & Routing

- `src/components/RequireRole.tsx` — route-level role guard; renders `<Outlet />` or redirects.
- `src/hooks/useRole.ts` — `useRole()` hook for component-level checks (`isAdmin`, `isOperator`, `can(['admin'])`).

To add a new admin-only page:
1. Create the page component.
2. Add route to `ROUTES` in `constants/routes.ts`.
3. Add it under `AdminOnly` children in `router.tsx`.

```tsx
// Component-level restriction
const { isAdmin, can } = useRole()
{isAdmin && <Button>Delete User</Button>}
{can(['admin']) && <AdminPanel />}
```

---

## 19. shadcn/ui

- Use shadcn components for **all** UI elements where one exists — do not build raw HTML equivalents.
- Import from `@/components/ui/*`.
- Never edit files under `src/components/ui/` manually — re-run the shadcn CLI instead.

---

## 20. UI Text Constants

- **Every user-visible string** in a component (labels, placeholders, button text, headings, error messages, tooltips, empty states) must be defined in the domain's constants file under `src/constants/`, never written inline in JSX.
- Name the object after the page/feature it belongs to, suffixed with `_TEXT`.
- Import and use the constant in the component — no bare string literals in JSX.

```ts
// ✅ src/constants/qr-gen.ts
export const QR_GEN_TEXT = {
  PAGE_TITLE: 'QR / Data Matrix Code Generator',
  SECTION_BATCH_DETAILS: 'Batch Details',
  SECTION_TRAY_SPECS: 'Tray Specs',
  BTN_GENERATE: 'Generate Codes',
  BTN_GENERATING: 'Generating…',
  PLACEHOLDER_BATCH: 'e.g. 2403',
  LABEL_VENDOR_CODE: 'Vendor Code',
  LABEL_LOT_NUMBER_OPTIONAL: 'Lot Number (optional)',
} as const
```

```tsx
// ✅ component — no bare strings
import { QR_GEN_TEXT } from '@/constants/qr-gen'

<h1>{QR_GEN_TEXT.PAGE_TITLE}</h1>
<Button>{isGenerating ? QR_GEN_TEXT.BTN_GENERATING : QR_GEN_TEXT.BTN_GENERATE}</Button>

// ❌ inline literal
<h1>QR / Data Matrix Code Generator</h1>
<Button>Generate Codes</Button>
```

---

## 21. Accessibility (aria labels)

- Every interactive element that lacks visible text (icon buttons, icon-only links, close buttons) **must** have `aria-label`.
- Every non-decorative image **must** have a meaningful `alt` attribute.
- Form inputs **must** be associated with their `<Label>` via matching `id` / `htmlFor` — never skip the `id`.
- Use `aria-busy` on elements that show loading state, `aria-live="polite"` on dynamic regions (toast containers, result counts).
- Landmark regions (`<header>`, `<main>`, `<nav>`, `<aside>`) should carry `aria-label` when multiple of the same landmark exist on a page.

```tsx
// ✅ icon-only button
<button aria-label={QR_GEN_TEXT.ARIA_CLOSE_MODAL} onClick={onClose}>
  <X size={14} />
</button>

// ✅ loading state
<Button aria-busy={isGenerating} disabled={isGenerating}>
  {isGenerating ? QR_GEN_TEXT.BTN_GENERATING : QR_GEN_TEXT.BTN_GENERATE}
</Button>

// ✅ form field
<Label htmlFor="vendorCode">{QR_GEN_TEXT.LABEL_VENDOR_CODE}</Label>
<Input id="vendorCode" ... />

// ❌ missing aria-label
<button onClick={onClose}><X size={14} /></button>
```

---

## 22. Assets (images & icons)

- All assets live under `src/assets/` — images in the root, SVG icons under `src/assets/icons/`.
- **`src/constants/assets.ts` is the single source of truth.** It imports every asset once and re-exports it. No other file imports directly from `@/assets/...`.
- SVG icons are imported as React components using Vite's `?react` suffix.
- PNG/JPG images are imported as URL strings via a normal import.

```ts
// ✅ src/constants/assets.ts
import CheckCircleIcon from '@/assets/icons/CheckCircle.svg?react'
import ExportIcon from '@/assets/icons/export-icon.svg?react'
import LogoImage from '@/assets/logo.png'
import GroupImage from '@/assets/Group.png'

export const ICONS = {
  CheckCircle: CheckCircleIcon,
  Export: ExportIcon,
} as const

export const IMAGES = {
  Logo: LogoImage,
  Group: GroupImage,
} as const
```

```tsx
// ✅ consuming component
import { ICONS, IMAGES } from '@/constants/assets'

<ICONS.CheckCircle aria-hidden="true" />
<img src={IMAGES.Logo} alt={COMMON_TEXT.ALT_LOGO} />

// ❌ direct asset import in a component
import CheckCircleIcon from '@/assets/icons/CheckCircle.svg?react'
```

- Add `assets.ts` to the barrel export in `src/constants/index.ts`.
- When a new asset is added to `src/assets/`, add its entry to `assets.ts` at the same time.

---

## 23. Types Location

- **All types and interfaces used by more than one file** must live in `src/types/` — one file per domain (e.g. `auth.ts`, `qr-gen.ts`).
- **Never define exported types directly inside a page, component, or service file.**
- A type used only within a single hook file may stay in that hook file as a non-exported local type (see §14).
- Import all shared types with `import type { ... }` from `@/types/<domain>`.

```ts
// ✅ src/types/qr-gen.ts
export interface EtchConfig { ... }
export interface EtchBatch { ... }

// ✅ consuming file
import type { EtchConfig, EtchBatch } from '@/types/qr-gen'

// ❌ type defined inside a page
// src/pages/qr-gen/index.tsx
export interface EtchConfig { ... }   // ← wrong location
```

---

## 24. Feature Development Checklist

Follow these steps **in order** every time you build a new page or feature. Do not skip steps or reorder them.

---

### Step 1 — Types (`src/types/<domain>.ts`)

Create the type file first. Everything else depends on it.

- One file per domain, plain `name.ts` (no compound extensions).
- `interface` for object shapes, `type` for unions/aliases.
- Export everything with named exports — no default exports from type files.

```ts
// src/types/user.ts
export interface AppUser {
  id: string
  fullName: string
  role: 'admin' | 'operator'
  isActive: boolean
  createdAt: string
}
export interface CreateUserPayload { ... }
export interface UpdateUserPayload { ... }
```

---

### Step 2 — Constants (`src/constants/<domain>.ts`)

Define all UI strings and static data before writing any JSX.

- All user-visible strings go in a `DOMAIN_TEXT` object (`as const`).
- Dropdown option arrays use `{ value, label }[]` shape.
- Add aria strings, toast messages, and button states here too.
- Re-export from `src/constants/index.ts` barrel.

```ts
// src/constants/user.ts
export const USER_TEXT = {
  PAGE_TITLE: 'Admin Settings',
  BTN_ADD_USER: '+ Add User',
  TOAST_CREATED: 'User created successfully.',
  ARIA_EDIT: (name: string) => `Edit ${name}`,
} as const
```

---

### Step 3 — Zod Schema (`src/lib/schemas/<domain>.ts`)

Define validation schemas before writing the hook. One schema per form.

#### File location & naming

- One file per domain under `src/lib/schemas/` — e.g. `auth.ts`, `user.ts`, `qr-gen.ts`.
- Plain `name.ts` — no compound extensions.
- Never put a schema inside a hook, component, or service file.
- Import in the hook with a named import: `import { createUserSchema } from '@/lib/schemas/user'`.

#### One schema per form, not per domain

Each distinct form submission gets its own schema. A domain may have several:

```ts
// src/lib/schemas/user.ts
import { z } from 'zod'

// Used by the Add User form
export const createUserSchema = z.object({
  fullName: z.string().min(1, 'Full name is required'),
  username: z.string().min(3, 'Username must be at least 3 characters'),
  password: z.string().min(8, 'Password must be at least 8 characters'),
  role: z.enum(['admin', 'operator'], { required_error: 'Role is required' }),
})

// Used by the Edit User form (all fields optional except shape)
export const editUserSchema = z.object({
  fullName: z.string().min(1, 'Full name is required'),
  role: z.enum(['admin', 'operator'], { required_error: 'Role is required' }),
  isActive: z.boolean(),
})
```

#### Mirror backend validators exactly

Check the backend `validators/` file for each route and copy the same rules. If the backend says `min(8)` for passwords, the frontend schema must also say `min(8)`. This avoids the frontend accepting input the backend will reject.

#### Common zod patterns used in this codebase

```ts
import { z } from 'zod'

// Required string
z.string().min(1, 'Field is required')

// String with length constraint
z.string().min(3, 'At least 3 characters').max(50, 'Max 50 characters')

// Fixed length (e.g. batch YYMM)
z.string().length(4, 'Must be 4 characters (YYMM)')

// Number
z.number().int().min(1, 'Must be at least 1').max(10000, 'Cannot exceed 10,000')

// Number coerced from string input (for <input type="number">)
z.coerce.number().int().min(1, 'Must be at least 1')

// Enum (role, status, etc.)
z.enum(['admin', 'operator'], { required_error: 'Role is required' })

// Optional field — still validates if provided
z.string().optional()
z.string().or(z.literal('')).optional()

// Boolean (switch/checkbox)
z.boolean()
```

#### How the hook uses the schema

Always call `.safeParse()` — never `.parse()` (which throws). Extract field errors using `.flatten()` and store them in a `Partial<Record<keyof FormShape, string>>` state. Clear a field's error when the user edits that field.

```ts
// In the hook
const [fieldErrors, setFieldErrors] = useState<Partial<Record<keyof CreateUserPayload, string>>>({})

async function handleCreate() {
  setFieldErrors({})

  const result = createUserSchema.safeParse(form)
  if (!result.success) {
    // flatten() gives { fieldErrors: { fieldName: string[] } }
    const flat = result.error.flatten().fieldErrors
    const errors: typeof fieldErrors = {}
    for (const key of Object.keys(flat) as (keyof typeof flat)[]) {
      errors[key] = flat[key]?.[0]   // take first message only
    }
    setFieldErrors(errors)
    return   // ← stop here, do NOT call the API
  }

  // result.data is fully typed and safe to pass to the service
  const user = await createUser(result.data)
}

// Clear error on keystroke
function setField<K extends keyof CreateUserPayload>(key: K, value: CreateUserPayload[K]) {
  setForm((prev) => ({ ...prev, [key]: value }))
  if (fieldErrors[key]) setFieldErrors((prev) => ({ ...prev, [key]: undefined }))
}
```

#### How the component displays field errors

Field errors are passed as a `fieldErrors` prop. Each input is paired with:
- `aria-invalid={!!fieldErrors.fieldName}` on the input
- `aria-describedby="fieldName-error"` on the input (only when error exists)
- `<FieldError>` from `@/components/field-error` below the input
- `<RequiredSign />` from `@/components/required-sign` inside the label

```tsx
import { RequiredSign } from '@/components/required-sign'
import { FieldError } from '@/components/field-error'

<Label htmlFor="username">
  {TEXT.LABEL_USERNAME}
  <RequiredSign />
</Label>
<Input
  id="username"
  value={form.username}
  onChange={(e) => setField('username', e.target.value)}
  aria-invalid={!!fieldErrors.username}
  aria-describedby={fieldErrors.username ? 'username-error' : undefined}
  aria-required="true"
/>
<FieldError id="username-error" message={fieldErrors.username} />
```

#### Rule summary

| Rule | Detail |
|------|--------|
| Location | `src/lib/schemas/<domain>.ts` only |
| One schema per form | create / edit / reset-password are separate schemas |
| Mirror backend | Copy the same `.min()` / `.max()` / `.enum()` values |
| Use `.safeParse()` | Never `.parse()` — it throws |
| Field errors = inline | Shown under the input, never as a toast |
| API errors = toast | Backend `{ error: "..." }` → `showErrorToast(...)` |
| Clear on change | Each `setField` call clears that field's error |

---

### Step 4 — Service (`src/services/<domain>.ts`)

Plain async functions. No hooks, no state, no error handling.

- One file per domain.
- Import the shared `api` instance from `@/lib/axios` — never create a new one.
- Import API endpoint strings from `@/constants/api`.
- Return typed data (using the types from Step 1).

```ts
// src/services/user.ts
export async function listUsers(page: number, limit: number): Promise<UserListResponse> {
  const { data } = await api.get(API_ENDPOINTS.USERS.LIST, { params: { page, limit } })
  return { data: data.data, pagination: data.pagination }
}
```

---

### Step 5 — Hook (`src/hooks/use<Domain>.ts`)

One hook owns everything: state, API calls, validation, toasts, navigation.

- Prefix with `use`, e.g. `useUsers`, `useQrGen`.
- Run zod `.safeParse()` **before** any API call. Show field errors inline, never toast.
- Catch API errors with `isAxiosError` — read `err.response?.data as ApiErrorBody` for the `error` field — show via `showErrorToast`.
- All handlers are `function` declarations (not arrow `const`).
- Derived values (filters, computed counts) are `const`.
- Return a flat object.

```ts
async function handleCreate() {
  const result = createUserSchema.safeParse(form)
  if (!result.success) {
    // set field errors — do NOT call API
    return
  }
  try {
    const user = await createUser(result.data)
    showSuccessToast(USER_TEXT.TOAST_CREATED)
  } catch (err) {
    if (isAxiosError(err)) {
      const body = err.response?.data as ApiErrorBody | undefined
      showErrorToast('Failed', body?.error)
    }
  }
}
```

---

### Step 6 — Page & Sub-components (`src/pages/<domain>/`)

```
src/pages/configuration/
├── index.tsx              ← page, consumes the hook only
├── user-form-modal.tsx    ← sub-component (pure UI, props only)
└── deactivate-confirm-modal.tsx  ← thin wrapper over ConfirmModal
```

#### Reusable ConfirmModal

For any delete / deactivate / destructive action use `ConfirmModal` from `@/components/confirm-modal` — do **not** create a new dialog from scratch each time.

```tsx
import { ConfirmModal } from '@/components/confirm-modal'

<ConfirmModal
  open={modal === 'delete'}
  title="Delete Session"
  description={`Are you sure you want to delete session ${selectedSession?.id}?`}
  confirmLabel="Delete"
  confirmingLabel="Deleting…"
  cancelLabel="Cancel"
  confirmVariant="destructive"   // 'default' | 'destructive' | 'outline' | 'secondary'
  isConfirming={isSaving}
  onConfirm={handleDelete}
  onClose={closeModal}
/>
```

Props reference:

| Prop | Type | Required | Default |
|------|------|----------|---------|
| `open` | `boolean` | ✅ | — |
| `title` | `string` | ✅ | — |
| `description` | `string` | ✅ | — |
| `confirmLabel` | `string` | ✅ | — |
| `confirmingLabel` | `string` | — | same as `confirmLabel` |
| `cancelLabel` | `string` | — | `'Cancel'` |
| `confirmVariant` | `ButtonProps['variant']` | — | `'destructive'` |
| `isConfirming` | `boolean` | — | `false` |
| `onConfirm` | `() => void` | ✅ | — |
| `onClose` | `() => void` | ✅ | — |

All text strings (`title`, `description`, `confirmLabel`, etc.) must come from a `DOMAIN_TEXT` constant — never passed as bare string literals from the page.

Rules:
- `index.tsx` calls the hook, passes everything down as props. **No logic inside the page.**
- Sub-components use a local unexported `interface Props`. Destructure in the signature.
- All strings come from `DOMAIN_TEXT` — no bare literals in JSX.
- Every icon-only button has `aria-label` from `DOMAIN_TEXT`.
- Every required form field has a `<RequiredSign />` marker and `aria-required="true"`.
- Field errors render below their input via `<FieldError />` with `role="alert"` and `aria-describedby` wired to the input.
- Loading/submit buttons have `aria-busy={isLoading}`.

#### Shared form primitives

Two shared components live in `src/components/` and must be used in every form — never recreate them inline:

```tsx
// src/components/required-sign.tsx
import { RequiredSign } from '@/components/required-sign'

<Label htmlFor="username">
  {TEXT.LABEL_USERNAME}
  <RequiredSign />   {/* renders red * — aria-hidden so screen readers skip it */}
</Label>
```

```tsx
// src/components/field-error.tsx
import { FieldError } from '@/components/field-error'

<Input
  id="username"
  aria-invalid={!!fieldErrors.username}
  aria-describedby={fieldErrors.username ? 'username-error' : undefined}
  aria-required="true"
/>
<FieldError id="username-error" message={fieldErrors.username} />
{/* renders nothing when message is undefined */}
```

#### Reusable ConfirmModal

For any destructive confirmation (delete, deactivate, reset) use `ConfirmModal` from `@/components/confirm-modal` — do **not** build a new dialog from scratch.

```tsx
import { ConfirmModal } from '@/components/confirm-modal'

<ConfirmModal
  open={modal === 'delete'}
  title={DOMAIN_TEXT.DELETE_TITLE}
  description={DOMAIN_TEXT.DELETE_BODY(item.name)}
  confirmLabel={DOMAIN_TEXT.BTN_DELETE}
  confirmingLabel={DOMAIN_TEXT.BTN_DELETING}
  cancelLabel={DOMAIN_TEXT.BTN_CANCEL}
  confirmVariant="destructive"
  isConfirming={isSaving}
  onConfirm={handleDelete}
  onClose={closeModal}
/>
```

`ConfirmModal` props:

| Prop | Type | Required | Default |
|------|------|----------|---------|
| `open` | `boolean` | ✅ | — |
| `title` | `string` | ✅ | — |
| `description` | `string` | ✅ | — |
| `confirmLabel` | `string` | ✅ | — |
| `confirmingLabel` | `string` | — | same as `confirmLabel` |
| `cancelLabel` | `string` | — | `'Cancel'` |
| `confirmVariant` | `ButtonProps['variant']` | — | `'destructive'` |
| `isConfirming` | `boolean` | — | `false` |
| `onConfirm` | `() => void` | ✅ | — |
| `onClose` | `() => void` | ✅ | — |

---

### Step 7 — Router (`src/router.tsx`)

- Add the new route under the correct layout (`AuthLayout` for protected, `AdminOnly` for admin-only).
- Use the route constant from `ROUTES`.

```ts
// Admin-only
{ path: ROUTES.CONFIGURATION, Component: ConfigurationPage }
```

---

### Step 8 — Route label (`src/constants/routes.ts`)

Add a display label to `ROUTE_LABELS` so the breadcrumb header works automatically.

```ts
export const ROUTE_LABELS: Record<string, string> = {
  '/configuration': 'Configuration',
}
```

---

### Step 9 — Sidebar (`src/constants/sidebar.tsx`)

Add a nav item if the page should appear in the sidebar. Use an icon from `ICONS`, restrict with `roles` if admin-only.

```ts
{
  title: 'Configuration',
  url: ROUTES.CONFIGURATION,
  icon: ICONS.Config,
  roles: ['admin'],
}
```

---

### Step 10 — Assets (if needed)

If the feature introduces new icons or images:
1. Drop the file into `src/assets/icons/` or `src/assets/`.
2. Add the import + export to `src/constants/assets.ts`.
3. Use `ICONS.NewIcon` or `IMAGES.NewImage` in components — never import directly from `@/assets/`.

---

### Complete file creation order (summary)

```
1. src/types/<domain>.ts
2. src/constants/<domain>.ts          (add to index.ts barrel)
3. src/lib/schemas/<domain>.ts
4. src/services/<domain>.ts
5. src/hooks/use<Domain>.ts
6. src/pages/<domain>/index.tsx
7. src/pages/<domain>/<sub-component>.tsx   (repeat as needed)
8. src/router.tsx                     (add route)
9. src/constants/routes.ts            (add ROUTE_LABELS entry)
10. src/constants/sidebar.tsx         (add nav item if needed)
```

---

## 25. Testing

**Framework:** Vitest (configured in `vite.config.ts`). Run with `pnpm test` from `apps/frontend/`.

### What to test

| Layer | Coverage target |
|-------|----------------|
| `src/lib/` pure functions | All branches — these are the easiest and highest-value tests |
| `src/lib/schemas/` | Valid input passes, each validation rule fails when violated |
| `src/store/` | Every action mutates state correctly; `clearAuth` resets to initial |
| `src/services/` | Each function calls the right endpoint with the right payload; errors propagate |
| `src/hooks/` | Logic-level only — mock `useAuthStore` / external deps; no React rendering |

### What NOT to test

- Pages and sub-components (UI rendering — covered by manual / E2E testing)
- The axios instance (`src/lib/axios.ts`) — always mock it in service tests
- Die-registration / fab-simulator mock services

### File locations

All test files live under `src/__tests__/`, mirroring the `src/` tree:

```
src/__tests__/
├── setup.ts                  # global stubs (localStorage, sessionStorage)
├── lib/
│   ├── utils.test.ts
│   ├── format.test.ts
│   └── tray-status.test.ts
├── schemas/
│   ├── auth.test.ts
│   ├── user.test.ts
│   └── qr-gen.test.ts
├── store/
│   └── auth.test.ts
└── services/
    ├── auth.test.ts
    ├── tray.test.ts
    └── scanner.test.ts
```

### Rules

**Naming:** `<subject>.test.ts` — no compound extensions.

**Mocking axios in service tests** — always mock the module, never the network:

```ts
vi.mock('@/lib/axios', () => ({
  default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
}))

// Retrieve mock after registration
const getApi = async () => (await import('@/lib/axios')).default
```

**Store tests** — reset state in `beforeEach` to prevent cross-test bleed:

```ts
beforeEach(() => {
  useAuthStore.setState({ user: null, token: null, refreshToken: null, isAuthenticated: false })
})
```

**Zod schema tests** — use `.safeParse()` and `.flatten().fieldErrors`:

```ts
const result = createUserSchema.safeParse({ ...valid, username: 'ab' })
expect(result.success).toBe(false)
expect(result.error!.flatten().fieldErrors.username).toBeDefined()
```

**Locale-sensitive date tests** — test structure (regex / `toContain`) not exact strings, unless the formatter uses a hardcoded locale like `'en-US'`.

**`vi.clearAllMocks()`** must be called in `beforeEach` in every service test file.

### Test environment

- Global environment: `node` (set in `vite.config.ts`)
- `localStorage` / `sessionStorage` are stubbed in `src/__tests__/setup.ts` — do not re-stub per file
- `jsdom` is **not** installed — do not write tests that require DOM APIs or React rendering



<!-- code-review-graph MCP tools -->
## MCP Tools: code-review-graph

**IMPORTANT: This project has a knowledge graph. ALWAYS use the
code-review-graph MCP tools BEFORE using Grep/Glob/Read to explore
the codebase.** The graph is faster, cheaper (fewer tokens), and gives
you structural context (callers, dependents, test coverage) that file
scanning cannot.

### When to use graph tools FIRST

- **Exploring code**: `semantic_search_nodes` or `query_graph` instead of Grep
- **Understanding impact**: `get_impact_radius` instead of manually tracing imports
- **Code review**: `detect_changes` + `get_review_context` instead of reading entire files
- **Finding relationships**: `query_graph` with callers_of/callees_of/imports_of/tests_for
- **Architecture questions**: `get_architecture_overview` + `list_communities`

Fall back to Grep/Glob/Read **only** when the graph doesn't cover what you need.

### Key Tools

| Tool | Use when |
|------|----------|
| `detect_changes` | Reviewing code changes — gives risk-scored analysis |
| `get_review_context` | Need source snippets for review — token-efficient |
| `get_impact_radius` | Understanding blast radius of a change |
| `get_affected_flows` | Finding which execution paths are impacted |
| `query_graph` | Tracing callers, callees, imports, tests, dependencies |
| `semantic_search_nodes` | Finding functions/classes by name or keyword |
| `get_architecture_overview` | Understanding high-level codebase structure |
| `refactor_tool` | Planning renames, finding dead code |

### Workflow

1. The graph auto-updates on file changes (via hooks).
2. Use `detect_changes` for code review.
3. Use `get_affected_flows` to understand impact.
4. Use `query_graph` pattern="tests_for" to check coverage.

## Architecture
- **Monorepo** managed by pnpm workspaces
- `apps/backend` — Express 5 + Prisma 7 + PostgreSQL 17 REST API
- `apps/frontend` — React 19 + Vite 8 + Tailwind 4 SPA
- `packages/shared` — Shared TypeScript types and constants

## Tech Stack
| Layer | Technology |
|-------|-----------|
| Runtime | Node.js >= 20 |
| Package manager | pnpm 10.29.2 (never use npm or yarn) |
| Backend | Express 5, Prisma 7, PostgreSQL 17, Zod 4 |
| Frontend | React 19, Vite 8, Tailwind CSS 4, React Router 7, Recharts 3 |
| Auth | JWT (jsonwebtoken) + bcrypt, HS256 signing |
| Testing | Vitest 4, supertest (backend), @vitest/coverage-v8 |
| Linting | ESLint 10 + typescript-eslint, husky pre-commit hooks |
| Deployment | Docker multi-stage builds, nginx for frontend |

## Common Commands
```bash
# Development
pnpm dev                    # Start frontend + backend in parallel
pnpm dev:backend            # Backend only (tsx watch)
pnpm dev:frontend           # Frontend only (vite)

# Build
pnpm build                  # Build all (shared → frontend + backend)
pnpm build:shared           # Build shared package only

# Testing
pnpm test                   # Run all tests
pnpm test:backend           # Backend tests (vitest)
pnpm test:frontend          # Frontend tests (vitest)
pnpm --filter @entegris/backend test:coverage  # Coverage report

# Database (Prisma)
pnpm db:generate            # Generate Prisma client
pnpm db:migrate             # Run migrations (dev)
pnpm db:seed                # Seed default data
pnpm db:studio              # Open Prisma Studio

# Linting
pnpm lint                   # Lint entire project

# Docker
pnpm docker:up              # Build and start all services
pnpm docker:down            # Stop all services
pnpm docker:dev             # Start only postgres + pgadmin
pnpm docker:logs            # Tail logs
```

## Project Structure
```
├── apps/
│   ├── backend/
│   │   ├── prisma/           # Schema, migrations, seed
│   │   └── src/
│   │       ├── config/       # DB connection, env validation
│   │       ├── middleware/    # Auth (JWT verify, role guard)
│   │       ├── routes/       # Express route handlers
│   │       ├── services/     # Business logic layer
│   │       ├── validators/   # Zod request validators
│   │       ├── openapi/      # Swagger/OpenAPI docs
│   │       ├── app.ts        # Express app setup
│   │       └── server.ts     # Entry point
│   └── frontend/
│       ├── src/
│       │   ├── lib/          # Utilities (cn helper)
│       │   └── styles/       # CSS / Tailwind
│       ├── nginx.conf        # Production reverse proxy
│       └── index.html        # SPA entry
└── packages/
    └── shared/
        └── src/
            ├── types.ts      # Shared interfaces & type aliases
            ├── constants.ts  # Business rule constants
            └── index.ts      # Re-exports
```

## Key Conventions
- **API prefix:** All endpoints under `/api/v1/`
- **Validation:** Zod schemas for all request validation (backend validators/)
- **Auth:** JWT middleware extracts `AuthPayload` (userId, username, role, sessionId)
- **Roles:** `admin` (full access) and `operator` (operational only)
- **Events are immutable:** Never edit/delete scan events or failures — insert only
- **Test files:** Co-located as `*.test.ts` next to source files
- **Imports:** Use `@entegris/shared` workspace package for shared types
- **TypeScript:** Strict mode enabled, target ES2022, ESM modules

## Database
- **ORM:** Prisma 7 with PostgreSQL adapter (`@prisma/adapter-pg`)
- **Schema:** `apps/backend/prisma/schema.prisma`
- **Generated client:** `apps/backend/generated/prisma/` (gitignored)
- **Migrations:** `apps/backend/prisma/migrations/`

## Business Rules
- Tray ID format: `VendorCode-TrayCode-MaterialCode-Batch-Serial`
- Die ID format: `Lot-WaferID-DeviceID` (free text, no enforcement)
- Pocket format: `R{row}C{col}` (R1C1 = top-left)
- Cycle limit: 20 (near-retirement at 80%)
- Duplicate scan window: 5 minutes
- Passwords: bcrypt with 10 rounds minimum
- One active test session at a time (DB enforced)

## Docker Services
| Service | Port | Image |
|---------|------|-------|
| Frontend | 80 | nginx:alpine (built from source) |
| Backend | 3001 | node:20-alpine (multi-stage) |
| PostgreSQL | 5432 | postgres:17.9-alpine |
| pgAdmin | configurable | dpage/pgadmin4:9.13 |


See `docs/Agents.md` for full agent specs and `docs/FRS.md` for requirements.

## Rules
- Always use `pnpm`, never `npm` or `yarn`
- Run `pnpm build:shared` before backend/frontend if shared types changed
- Run `pnpm db:generate` after any Prisma schema change
- Keep test files co-located with source (`*.test.ts`)
- Use Zod for all input validation — no manual validation
- Never log sensitive data (passwords, tokens)
- All state changes must be timestamped and attributed to a user
- **Every new API endpoint must have:** (1) unit tests in `*.service.test.ts` covering happy path + error cases, and (2) OpenAPI/Swagger docs registered in `openapi/routes/*.routes.ts` and `openapi/schemas/*.ts` — never skip either
