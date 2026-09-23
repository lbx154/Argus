"""HTTP route registrars accept the app and its ServerContext.

Routes call the owning service modules directly; daemon services and query
workers remain isolated in the context constructed by create_app."""
