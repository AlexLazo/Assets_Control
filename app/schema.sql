PRAGMA foreign_keys = ON;

CREATE TABLE rutas (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo      TEXT NOT NULL UNIQUE,
    supervisor  TEXT,
    activa      BOOLEAN NOT NULL DEFAULT 1
);

CREATE TABLE usuarios (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre         TEXT NOT NULL UNIQUE,
    password_hash  TEXT NOT NULL,
    rol            TEXT NOT NULL DEFAULT 'supervisor' CHECK (rol IN ('supervisor','facturador','admin','super_admin')),
    activo         BOOLEAN NOT NULL DEFAULT 1
);

CREATE TABLE equipos (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    id_interno         TEXT NOT NULL UNIQUE,
    tipo               TEXT NOT NULL CHECK (tipo IN ('telefono','impresora')),
    serial_fabrica     TEXT,
    numero_telefono    TEXT,
    pin_equipo         TEXT,
    modelo             TEXT,
    fabricante         TEXT,
    estado             TEXT NOT NULL DEFAULT 'activo' CHECK (estado IN ('activo','reparacion','baja')),
    notas              TEXT,
    requiere_revision  BOOLEAN NOT NULL DEFAULT 0,
    ruta_asignada_id   INTEGER REFERENCES rutas(id),
    fecha_alta         TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE movimientos (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    equipo_id    INTEGER NOT NULL REFERENCES equipos(id),
    ruta_id      INTEGER NOT NULL REFERENCES rutas(id),
    operador_id  INTEGER NOT NULL REFERENCES usuarios(id),
    tipo         TEXT NOT NULL CHECK (tipo IN ('salida','retorno')),
    timestamp    TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    condicion    TEXT
);
CREATE INDEX idx_movimientos_equipo    ON movimientos(equipo_id, id);
CREATE INDEX idx_movimientos_timestamp ON movimientos(timestamp);

CREATE TABLE incidencias (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    equipo_id             INTEGER NOT NULL REFERENCES equipos(id),
    ruta_id               INTEGER NOT NULL REFERENCES rutas(id),
    salida_movimiento_id  INTEGER NOT NULL REFERENCES movimientos(id),
    fecha                 TEXT NOT NULL,
    fecha_deteccion       TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    operador_cierre_id    INTEGER REFERENCES usuarios(id),
    estado                TEXT NOT NULL DEFAULT 'abierta' CHECK (estado IN ('abierta','resuelta')),
    resolucion_nota       TEXT,
    UNIQUE (equipo_id, fecha)
);

CREATE TABLE mantenimientos (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    equipo_id            INTEGER NOT NULL REFERENCES equipos(id),
    fecha_envio          TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    fecha_retorno        TEXT,
    motivo               TEXT,
    resultado            TEXT,
    operador_envio_id    INTEGER NOT NULL REFERENCES usuarios(id),
    operador_retorno_id  INTEGER REFERENCES usuarios(id),
    estado               TEXT NOT NULL DEFAULT 'en_reparacion' CHECK (estado IN ('en_reparacion','resuelto'))
);
CREATE INDEX idx_mantenimientos_equipo ON mantenimientos(equipo_id, id);

CREATE VIEW v_estado_actual AS
SELECT
    e.id AS equipo_id,
    e.id_interno,
    e.tipo AS tipo_equipo,
    m.tipo AS ultimo_movimiento,
    m.ruta_id AS ruta_actual_id,
    m.timestamp AS desde,
    CASE WHEN m.tipo = 'salida' THEN 'en_ruta' ELSE 'en_bodega' END AS ubicacion
FROM equipos e
LEFT JOIN movimientos m
    ON m.id = (
        SELECT m2.id FROM movimientos m2
        WHERE m2.equipo_id = e.id
        ORDER BY m2.id DESC
        LIMIT 1
    );

CREATE TRIGGER trg_no_doble_salida
BEFORE INSERT ON movimientos
WHEN NEW.tipo = 'salida' AND (
    SELECT m.tipo FROM movimientos m
    WHERE m.equipo_id = NEW.equipo_id
    ORDER BY m.id DESC LIMIT 1
) IS 'salida'
BEGIN
    SELECT RAISE(ABORT, 'Este equipo ya tiene una salida activa; registre su retorno antes de una nueva salida.');
END;

CREATE TRIGGER trg_no_retorno_sin_salida
BEFORE INSERT ON movimientos
WHEN NEW.tipo = 'retorno' AND (
    SELECT m.tipo FROM movimientos m
    WHERE m.equipo_id = NEW.equipo_id
    ORDER BY m.id DESC LIMIT 1
) IS NOT 'salida'
BEGIN
    SELECT RAISE(ABORT, 'Este equipo no tiene una salida activa; no se puede registrar un retorno.');
END;

CREATE TRIGGER trg_no_salida_si_no_activo
BEFORE INSERT ON movimientos
WHEN NEW.tipo = 'salida' AND (
    SELECT estado FROM equipos WHERE id = NEW.equipo_id
) != 'activo'
BEGIN
    SELECT RAISE(ABORT, 'Este equipo no está activo (en reparación o dado de baja); no se puede registrar una salida.');
END;

CREATE TRIGGER trg_no_doble_mantenimiento
BEFORE INSERT ON mantenimientos
WHEN NEW.estado = 'en_reparacion' AND EXISTS (
    SELECT 1 FROM mantenimientos WHERE equipo_id = NEW.equipo_id AND estado = 'en_reparacion'
)
BEGIN
    SELECT RAISE(ABORT, 'Este equipo ya tiene un mantenimiento abierto.');
END;

CREATE TABLE conteos (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha_inicio        TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    fecha_cierre        TEXT,
    operador_inicio_id  INTEGER NOT NULL REFERENCES usuarios(id),
    estado              TEXT NOT NULL DEFAULT 'abierto' CHECK (estado IN ('abierto','cerrado')),
    ajuste_aplicado     BOOLEAN NOT NULL DEFAULT 0,
    notas               TEXT
);
CREATE UNIQUE INDEX idx_un_solo_conteo_abierto ON conteos(estado) WHERE estado = 'abierto';

CREATE TABLE conteo_items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    conteo_id    INTEGER NOT NULL REFERENCES conteos(id),
    equipo_id    INTEGER NOT NULL REFERENCES equipos(id),
    operador_id  INTEGER NOT NULL REFERENCES usuarios(id),
    timestamp    TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (conteo_id, equipo_id)
);
