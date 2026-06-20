# Frontend Interview Prep — Shakib S.

## Tech Stack: React, Next.js, TypeScript, Tailwind CSS

---

## 1. React Fundamentals

### Core Concepts to Master

**Component Lifecycle:**
- **Functional components** (current standard)
- **Hooks**: `useState`, `useEffect`, `useContext`, `useReducer`
- **Custom hooks** for reusable logic
- **Render cycle**: render → commit → effect

**State Management Hierarchy:**
```
Local State (useState) 
  ↓ Shared state (context/useContext) 
  ↓ Global state (Zustand, Redux Toolkit)
  ↓ Server state (React Query / SWR)
```

### Interview Questions

1. **"What are React hooks and why were they introduced?"**
   - Enable functional components to use state/lifecycle
   - Replaced class components (`this.state`, `componentDidMount`)
   - Rules: only call at top level, never conditionally

2. **"useEffect cleanup — when does it run?"**
   - Runs on unmount AND before re-executing effect
   - Essential for: subscriptions, timers, event listeners
   - Example:
     ```typescript
     useEffect(() => {
       const sub = api.subscribe(handler)
       return () => sub.unsubscribe()  // cleanup
     }, [])
     ```

3. **"React.memo vs useMemo vs useCallback?"**
   - `React.memo` — memoizes component **renders** (prevents unnecessary re-renders)
   - `useMemo` — memoizes **computed values** (expensive calculations)
   - `useCallback` — memoizes **functions** (prevents prop reference changes)
   - Rule of thumb: optimize only when profiling shows it's needed

---

## 2. Next.js Architecture

### Page vs App Router

**Pages Router:**
- File-based routing (`pages/users.tsx`)
- `getServerSideProps`, `getStaticProps` for data fetching
- Good for: SSR, SSG, hybrid approaches

**App Router (Next.js 13+):**
- Server Components by default
- Client Components with `"use client"` directive
- Route segments: `layout`, `page`, `loading`, `error`
- Streaming with Suspense

### Interview Questions

1. **"Server vs Client Components?"**
   - **Server**: runs on edge/server, no browser JS, faster initial load, can access DB directly
   - **Client**: interactive UI, hooks, event handlers, browser-only
   - Strategy: render server-side, hydrate client-side where needed

2. **"How do you handle data fetching in Next.js?"**
   - `getServerSideProps` (SSR) — every request
   - `getStaticProps` (SSG) — build-time only
   - `fetch` + `cache` tags (App Router)
   - React Query for client-side caching

3. **"Explain Next.js image optimization"**
   - `<NextImage>` automatically optimizes (sizes, formats WebP/AVIF)
   - Lazy loading by default
   - CDN caching headers

---

## 3. TypeScript & Type Safety

### Key Concepts

**Type Guards:**
```typescript
function handleUser(data: unknown): User {
    if (isUser(data)) return data  // type narrowing
    throw new Error("Invalid user")
}
```

**Common Patterns:**
- `Partial<T>`, `Required<T>`, `Pick<T, K>` — utility types
- Discriminated unions for state machines: `{type: "loading"} | {type: "success", data: ...}`
- Generic components for reusable typed UIs

### Interview Questions

1. **"How do you handle API response types?"**
   - Define strict interfaces for every response shape
   - Use Zod or Yup for runtime validation + type inference
   - Example:
     ```typescript
     import { z } from "zod"
     const UserSchema = z.object({ id: z.number(), name: z.string() })
     type User = z.infer<typeof UserSchema>  // inferred type!
     ```

2. **"When would you use `as` vs generic?"**
   - Prefer generics (type-safe, propagates through code)
   - Use `as` only for known-safety casts or library limitations

---

## 4. State Management Patterns

### Your Stack: Zustand + React Query

**Zustand** (Client state):
```typescript
import { create } from "zustand"

interface ChatStore {
    messages: Message[]
    addMessage: (msg: Message) => void
}

const useChatStore = create<ChatStore>((set) => ({
    messages: [],
    addMessage: (msg) => set((state) => ({ 
        messages: [...state.messages, msg] 
    }))
}))
```

**React Query** (Server state):
- Automatic caching, background refetching, optimistic updates
- `useQuery` for fetching, `useMutation` for mutations
- Cache invalidation on success/error

### Interview Questions

1. **"Client state vs Server state?"**
   - Client: UI state (modals, form fields, selected items) → Zustand/Context
   - Server: cached API data → React Query / SWR
   - Rule: never put server response in local state without sync strategy

2. **"How do you handle form validation?"**
   - React Hook Form + Zod for schema-based validation
   - `react-hook-form` handles submission lifecycle, focus management
   - Zod validates on submit AND shows field-level errors

---

## 5. Performance Optimization

### Key Metrics (Core Web Vitals)
- **LCP** (Largest Contentful Paint): < 2.5s
- **FID** / **INP** (Interaction to Paint): < 100ms
- **CLS** (Cumulative Layout Shift): < 0.1

### Optimization Techniques

**Code Splitting:**
```typescript
// Next.js dynamic import
const LazyComponent = dynamic(() => import("./HeavyComponent"), {
    loading: () => <p>Loading...</p>
})
```

**Memoization Strategy:**
1. Profile first (`react-window`, `why-did-you-render`)
2. Add `React.memo` to frequently re-rendering components
3. Use `useMemo` for expensive computations
4. Virtualize long lists (`react-window`, `react-virtualized`)

**Bundle Size:**
- Tree shaking with webpack/next config
- Lazy load non-critical routes
- Monitor bundle: `next build && next bundle analyze`

### Interview Questions

1. **"How do you debug React performance?"**
   - React DevTools Profiler (record renders)
   - Chrome Performance tab (timeline, memory snapshots)
   - `why-did-you-render` library for unnecessary re-renders
   - Lighthouse for real-world metrics

2. **"Explain virtual DOM diffing"**
   - React creates a virtual tree representation
   - On update, generates new virtual tree
   - Diff algorithm (Fiber) finds minimal changes
   - Batch updates for performance

---

## 6. UI/UX Patterns & Accessibility

### Component Patterns

**Compound Components:**
```typescript
// Table + TableRow pattern
<Table>
  <TableRow><TableCell>...</TableCell></TableRow>
</Table>
```

**Render Props / Custom Hooks:**
- Extract logic into hooks (`useAuth`, `useLocalStorage`)
- Keep components focused on UI only

### Accessibility (a11y)
- Semantic HTML (`<button>` not `<div onClick>`)
- ARIA attributes when needed (`aria-label`, `aria-expanded`)
- Keyboard navigation support
- Color contrast ratios (WCAG AA minimum)
- Screen reader testing tools

### Interview Questions

1. **"How do you make a React app accessible?"**
   - Start with semantic HTML
   - Test with axe-core / Lighthouse accessibility audits
   - Ensure keyboard navigability (Tab, Enter, Escape)
   - Use `aria-*` only when native elements don't fit

2. **"Design a reusable modal component"**
   - Portal to root for overlay rendering
  焦点管理（trap focus inside modal）
   - Close on Escape + backdrop click
   - Props: isOpen, onClose, title, children, variant

---

## 7. Testing Frontend Code

### Stack: Jest + React Testing Library + Cypress

**Unit Tests (Jest + RTL):**
```typescript
import { render, screen, fireEvent } from "@testing-library/react"

test("shows error on invalid form", () => {
    render(<LoginForm />)
    fireEvent.submit(screen.getByRole("button"))
    expect(screen.getByText("Email required")).toBeInTheDocument()
})
```

**Integration Tests (Cypress):**
- Full user flows: login → dashboard → action
- Network stubbing with `cy.intercept()`
- CI/CD integration

### Interview Questions

1. **"Testing strategy for React?"**
   - Unit: test pure logic, custom hooks, utility functions
   - Integration: test component behavior (RTL — render + interact + assert)
   - E2E: critical user flows (Cypress, Playwright)
   - Coverage: aim for 70%+ on business logic

2. **"How do you mock API calls in tests?"**
   - MSW (Mock Service Worker) for realistic API mocking
   - `cy.intercept()` in Cypress
   - React Query's `queryClient.setQueryData` for cache control

---

## 8. Frontend Architecture

### Folder Structure (Feature-Based)
```
src/
├── components/    # Shared UI (Button, Modal, Table)
├── features/      # Domain modules
│   ├── auth/
│   │   ├── components/
│   │   ├── hooks/
│   │   └── api.ts
│   └── chat/
├── lib/           # Utilities, constants
└── types/         # Global type definitions
```

### Code Organization Principles
- **Single Responsibility**: one component per file (or feature)
- **Colocation**: API calls co-located with components that use them
- **Shared logic**: extract to custom hooks or utilities

---

## Quick Interview Checklist

- [ ] Can explain React lifecycle and hooks
- [ ] Know when to use Server vs Client Components
- [ ] Can design a reusable component (modal, table, form)
- [ ] Understand state management tradeoffs
- [ ] Can explain Core Web Vitals and optimization strategies
- [ ] Know how to test React components
- [ ] Familiar with TypeScript patterns (generics, discriminated unions)
- [ ] Can discuss accessibility best practices
