import { validateEnv, AppEnv, DatabaseDriver } from './env.js';

/** External task-platform (Jira) configuration, fully resolved. */
export class JiraConfig {
  /** `disabled` | `mock` | `live` */
  mode: 'disabled' | 'mock' | 'live' = 'disabled';
  baseUrl: string | undefined = undefined;
  email: string | undefined = undefined;
  apiToken: string | undefined = undefined;
  projectKey: string | undefined = undefined;
  epicKey: string | undefined = undefined;
  /** requestType → Jira issue type name (default `Task`). */
  issueTypeMap: Record<string, string> = {};
  /** `full` | `masked` | `none` — what callers' details may leave the practice. */
  piiMode: 'full' | 'masked' | 'none' = 'masked';
  timeoutMs = 5000;

  /** True when a live sync could actually be attempted. */
  get ready(): boolean {
    if (this.mode !== 'live') return true;
    return Boolean(this.baseUrl && this.email && this.apiToken && this.projectKey);
  }
}

/**
 * Fully typed, pre-validated application configuration shared by main.ts, the
 * database module and the tests. Built once from `EnvVariables` so nothing
 * downstream has to re-coerce environment strings.
 */
export class AppConfig {
  driver: DatabaseDriver = DatabaseDriver.Sqlite;
  dbUrl: string | undefined = undefined;
  sqlitePath: string = './data/practice.db';
  port: number = 3000;
  env: AppEnv = AppEnv.Development;
  corsOrigins: string[] = [];
  jira = new JiraConfig();
}

export const APP_CONFIG = 'APP_CONFIG';

/** Parses `JIRA_ISSUE_TYPE_MAP` (`requestType:IssueType,...`). */
function parseIssueTypeMap(raw: string | undefined): Record<string, string> {
  const map: Record<string, string> = {};
  if (!raw) return map;
  for (const entry of raw.split(',')) {
    const [requestType, issueType] = entry.split(':').map((part) => part.trim());
    if (requestType && issueType) {
      map[requestType] = issueType;
    }
  }
  return map;
}

/** Builds the typed configuration from the (validated) environment. */
export function buildAppConfig(): AppConfig {
  const env = validateEnv(process.env as Record<string, unknown>);
  const config = new AppConfig();
  config.driver = env.DB_DRIVER;
  config.dbUrl = env.DB_URL;
  config.sqlitePath = env.SQLITE_PATH;
  config.port = env.PORT;
  config.env = env.APP_ENV;
  config.corsOrigins = env.CORS_ORIGINS.split(',')
    .map((origin) => origin.trim())
    .filter((origin) => origin.length > 0);

  const jira = new JiraConfig();
  jira.mode = env.JIRA_MODE;
  jira.baseUrl = env.JIRA_BASE_URL?.replace(/\/+$/, '') || undefined;
  jira.email = env.JIRA_EMAIL;
  jira.apiToken = env.JIRA_API_TOKEN;
  jira.projectKey = env.JIRA_PROJECT_KEY;
  jira.epicKey = env.JIRA_EPIC_KEY;
  jira.issueTypeMap = parseIssueTypeMap(env.JIRA_ISSUE_TYPE_MAP);
  jira.piiMode = env.JIRA_PII_MODE;
  jira.timeoutMs = env.JIRA_TIMEOUT_MS;
  config.jira = jira;

  return config;
}
