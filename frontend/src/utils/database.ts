export type DatabaseType = 'mysql' | 'postgresql'

export const DEFAULT_DATABASE_PORT: Record<DatabaseType, number> = {
  mysql: 3306,
  postgresql: 5432,
}

/** 切换类型时跟随缺省端口，保留用户填写的其他端口。 */
export function portForDatabaseChange(previous: DatabaseType, next: DatabaseType, port?: number | null) {
  return port == null || port === DEFAULT_DATABASE_PORT[previous] ? DEFAULT_DATABASE_PORT[next] : port
}
