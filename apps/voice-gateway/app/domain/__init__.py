"""Provider-independent domain layer (events, audio, sessions).

The rest of the application (routes, services) must depend on these types only —
never on provider-specific JSON shapes, which live in `app.integrations.<provider>`.
"""
