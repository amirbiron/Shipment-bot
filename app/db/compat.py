"""
פונקציות SQL תואמות דיאלקט — PostgreSQL + SQLite.

מכיל פונקציות SQL שמתרגמות אוטומטית לדיאלקט הנכון
(PostgreSQL בפרודקשן, SQLite בבדיקות).
"""
from sqlalchemy import String
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.functions import GenericFunction


class year_month(GenericFunction):
    """חילוץ שנה-חודש (YYYY-MM) מעמודת datetime — תואם PostgreSQL ו-SQLite."""
    type = String()
    name = "year_month"
    inherit_cache = True


@compiles(year_month, "postgresql")
def _pg_year_month(element, compiler, **kw):
    """PostgreSQL: to_char(col, 'YYYY-MM')"""
    col = compiler.process(element.clauses.clauses[0], **kw)
    return f"to_char({col}, 'YYYY-MM')"


@compiles(year_month, "sqlite")
def _sqlite_year_month(element, compiler, **kw):
    """SQLite: strftime('%Y-%m', col)"""
    col = compiler.process(element.clauses.clauses[0], **kw)
    return f"strftime('%Y-%m', {col})"
