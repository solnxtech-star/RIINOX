# Sign in with Google: setup and frontend guide

RIINOX supports **Google only** as a social login. One button signs people **in or up**. The backend endpoint is
`POST /api/auth/google/` (full reference in Swagger, section **auth**).

---

## 1. How it works

```
Browser                         Google                       RIINOX API
  |  click "Sign in with Google"  |                              |
  |------------------------------>|                              |
  |  credential (signed ID token) |                              |
  |<------------------------------|                              |
  |  POST /api/auth/google/ {"credential": "..."}                |
  |------------------------------------------------------------->|  verifies signature, audience,
  |                                                              |  expiry, verified email
  |  {access, refresh, setup_info, registration_complete,        |  finds / links / creates user
  |   is_new_user}                                               |
  |<-------------------------------------------------------------|
```

There are no redirects, the backend never sees a Google password, and no client secret is involved: the
backend only needs the **client id** to check the token was issued for RIINOX.

---

## 2. One-time setup in Google Cloud Console (backend owner)

1. Go to <https://console.cloud.google.com/> and create or select a project (e.g. "RIINOX").
2. Open **APIs & Services → OAuth consent screen** (also called "Google Auth Platform → Branding").
   - User type: **External**.
   - App name **RIINOX**, a support email, and the logo (optional).
   - Authorized domain: `riinox.com`.
   - Links to the privacy policy and terms of service pages (needed to publish).
   - Scopes: only `openid`, `email`, `profile` (the defaults). These need no Google review.
   - **Publish the app** ("In production"). While it says "Testing", only the test users you list can sign in.
3. Open **Credentials → Create credentials → OAuth client ID**.
   - Application type: **Web application**.
   - **Authorized JavaScript origins:**
     - `https://app.riinox.com`
     - `http://localhost:3000` and `http://localhost` (local development; Google requires both for localhost)
   - **Authorized redirect URIs:** none needed.
4. Copy the **Client ID** (looks like `1234-abc.apps.googleusercontent.com`). You don't need the client secret.

A separate client per environment (staging, production) is fine: list them all, comma-separated.

---

## 3. Configuration

| Where | Variable | Value |
|---|---|---|
| Backend (Render / server env, and `.envs/.local/.django`) | `GOOGLE_OAUTH_CLIENT_IDS` | The client id(s), comma-separated |
| Frontend | e.g. `NEXT_PUBLIC_GOOGLE_CLIENT_ID` | The same client id |

If the backend variable is empty, Google sign-in is off and the endpoint answers
`503 GOOGLE_SIGN_IN_NOT_CONFIGURED`. The client id is public (it appears in the page), so it isn't a secret.

---

## 4. Frontend

Load Google Identity Services once:

```html
<script src="https://accounts.google.com/gsi/client" async></script>
```

Render the button and send the credential to the API:

```js
google.accounts.id.initialize({
  client_id: process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID,
  callback: async ({ credential }) => {
    const res = await fetch(`${API_URL}/api/auth/google/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ credential }),
    });
    const body = await res.json();
    if (!res.ok) return showError(body.code, body.detail);
    saveTokens(body.access, body.refresh);      // exactly as after email login
    routeAfterLogin(body);                       // same as email login
  },
});
google.accounts.id.renderButton(document.getElementById("google-btn"), {
  theme: "outline", size: "large", text: "continue_with", width: 320,
});
```

(React apps can use `@react-oauth/google`'s `<GoogleLogin onSuccess={({credential}) => ...} />`, which does the
same thing.)

Put the same button on **both** the login and the sign-up page. There is no separate "sign up with Google"
call.

### The response

Same as `POST /api/auth/jwt/login/`, plus `is_new_user`:

```json
{
  "access": "eyJ...",
  "refresh": "eyJ...",
  "setup_info": {"id": 42, "email": "ada@gmail.com", "name": "Ada Obi"},
  "registration_complete": true,
  "is_new_user": true
}
```

`201` = a new account was created, `200` = signed in to an existing one. After this, everything is the same as
email login, then load the user's organizations.

**Tokens (same for every login method):** the `access` token lasts **60 minutes**. When the API answers `401`,
call `POST /api/auth/jwt/refresh/` with `{"refresh": ...}`: it returns a new `access` **and a new `refresh`**.
Store both; the old refresh token stops working. To log out, call `POST /api/users/logout/` with
`{"refresh": ...}` (or `{"all": true}` for every device), then delete both tokens. A user with no
organization goes to "Create organization"; a user who arrived from an invitation link accepts it as usual.

### What happens to each kind of user

| Situation | Result |
|---|---|
| Google account used before | Signed in to the same RIINOX account, even if their Gmail address changed |
| An email/password account with the same email exists | **Linked automatically** and signed in. Email login keeps working too. |
| Signed up with email but never clicked the activation link | Activated (Google proved the email) and signed in |
| New person | Account created, verified, named from Google, **no password**. To also use email login later: "Forgot password". |
| Account deactivated by RIINOX | Refused (`403 ACCOUNT_DISABLED`) |

### Errors to handle

| `code` | Status | Show |
|---|---|---|
| `GOOGLE_TOKEN_INVALID` | 401 | "Google sign-in failed, please try again." (expired, forged or wrong app) |
| `GOOGLE_EMAIL_NOT_VERIFIED` | 403 | Ask them to verify their Google email, or sign up with email |
| `ACCOUNT_DISABLED` | 403 | "This account has been deactivated. Contact support." |
| `GOOGLE_SIGN_IN_NOT_CONFIGURED` | 503 | Hide the Google button (server not configured) |
| `GOOGLE_UNAVAILABLE` | 503 | "Couldn't reach Google, try again." |
| (throttled) | 429 | More than 20 attempts a minute from one address |

---

## 5. Checklist

- [ ] OAuth consent screen filled in and **published**
- [ ] Web client created with the production and localhost JavaScript origins
- [ ] `GOOGLE_OAUTH_CLIENT_IDS` set on the backend; same id in the frontend env
- [ ] Button on login and sign-up pages, posting `credential` to `/api/auth/google/`
- [ ] Tokens stored and post-login routing identical to email login
- [ ] Error codes above handled
