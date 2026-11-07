import { QueryFailedError } from 'typeorm';

/** Best-effort driver error code extraction (mysql2 / better-sqlite3). */
export function readDriverCode(error: QueryFailedError): string | undefined {
  const driverError: unknown = error.driverError;
  if (
    typeof driverError === 'object' &&
    driverError !== null &&
    'code' in driverError &&
    typeof (driverError as { code: unknown }).code === 'string'
  ) {
    return (driverError as { code: string }).code;
  }
  return undefined;
}

/** True when the query failed because a unique/duplicate constraint hit. */
export function isDuplicateKeyError(error: unknown): boolean {
  if (!(error instanceof QueryFailedError)) return false;
  const code = readDriverCode(error);
  return (
    code === 'ER_DUP_ENTRY' ||
    code === 'ER_DUP_KEY' ||
    code === '23505' ||
    code === 'SQLITE_CONSTRAINT_PRIMARYKEY' ||
    code === 'SQLITE_CONSTRAINT_UNIQUE' ||
    /UNIQUE constraint failed/i.test(error.message)
  );
}

/** True when the query failed because a foreign-key constraint hit. */
export function isForeignKeyViolationError(error: unknown): boolean {
  if (!(error instanceof QueryFailedError)) return false;
  const code = readDriverCode(error);
  return (
    code === 'ER_NO_REFERENCED_ROW_2' ||
    code === 'ER_ROW_IS_REFERENCED_2' ||
    code === 'ER_NO_REFERENCED_ROW' ||
    code === 'ER_ROW_IS_REFERENCED' ||
    code === 'SQLITE_CONSTRAINT_FOREIGNKEY' ||
    /foreign key constraint/i.test(error.message)
  );
}
