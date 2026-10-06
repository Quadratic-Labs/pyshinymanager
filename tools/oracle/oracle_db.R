#!/usr/bin/env Rscript
# Oracle CONTRAT pour le module db : cree une base via create_db du package SOURCE (sans
# passphrase -> tables ecrites en clair, donc le contenu logique est lisible) et renvoie les 3
# tables telles que read_db_decrypt les restitue. Sert de reference pour le contenu logique
# (schema, ordre des colonnes, initialisation pwd_mngt/logs), le stockage physique differant
# volontairement cote Python (D4/D7).
#
# Usage : Rscript oracle_db.R <out.json> <credentials.json>

suppressWarnings(suppressMessages({ library(jsonlite); library(scrypt) }))

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 2) stop("Usage: Rscript oracle_db.R <out.json> <credentials.json>")
out_file <- args[[1]]
cred_file <- args[[2]]

this_dir <- dirname(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)))
if (length(this_dir) == 0 || !nzchar(this_dir)) this_dir <- "."
src <- normalizePath(file.path(this_dir, "..", "..", "..", "source", Sys.getenv("SM_SOURCE", "shinymanager-1.1.1.1")), mustWork = TRUE)
suppressWarnings(suppressMessages(pkgload::load_all(src, quiet = TRUE, export_all = TRUE)))

cred <- fromJSON(cred_file, simplifyDataFrame = TRUE)

tmp <- tempfile(fileext = ".sqlite")
create_db(credentials_data = cred, sqlite_path = tmp, passphrase = NULL)

read_tbl <- function(name) {
  df <- read_db_decrypt(tmp, name, passphrase = NULL)
  list(columns = as.list(colnames(df)), rows = if (nrow(df) == 0) list() else df)
}

res <- list(
  credentials = read_tbl("credentials"),
  pwd_mngt = read_tbl("pwd_mngt"),
  logs = read_tbl("logs"),
  today = as.character(Sys.Date())
)

writeLines(toJSON(res, auto_unbox = TRUE, null = "null", na = "null", dataframe = "rows", pretty = TRUE),
           out_file, useBytes = TRUE)
cat("oracle db ecrit ->", out_file, "\n")
