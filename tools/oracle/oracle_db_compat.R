#!/usr/bin/env Rscript
# Oracle de compatibilite des bases SQLite R <-> Python (chiffrees et en clair).
#
# Usage :
#   Rscript oracle_db_compat.R <out.json> r_write <dir>
#       R cree <dir>/r_enc.sqlite (passphrase "secret") et <dir>/r_plain.sqlite, modifie
#       pwd_mngt et logs, puis exporte le contenu lu par read_db_decrypt.
#   Rscript oracle_db_compat.R <out.json> r_read <path>
#       R lit la base chiffree ecrite par Python (passphrase "secret"), teste un login, puis
#       ecrit une modification (must_change de victor) que Python devra relire.
# Les valeurs sont exportees en texte (as.character, NA -> null) pour une comparaison simple.

suppressWarnings(suppressMessages({ library(jsonlite) }))

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 3) stop("Usage: Rscript oracle_db_compat.R <out.json> r_write|r_read <arg>")
out_file <- args[[1]]
mode <- args[[2]]
target <- args[[3]]

this_dir <- dirname(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)))
if (length(this_dir) == 0 || !nzchar(this_dir)) this_dir <- "."
src <- normalizePath(Sys.getenv("SM_SOURCE_PATH", file.path(this_dir, "..", "..", "..", "source", Sys.getenv("SM_SOURCE", "shinymanager"))), mustWork = TRUE)
suppressWarnings(suppressMessages(pkgload::load_all(src, quiet = TRUE, export_all = TRUE)))

as_text <- function(df) {
  df[] <- lapply(df, function(x) if (inherits(x, "Date")) as.character(x) else as.character(x))
  list(columns = colnames(df), rows = df)
}
dump_db <- function(path, passphrase) {
  lapply(stats::setNames(nm = c("credentials", "pwd_mngt", "logs")), function(t) {
    as_text(read_db_decrypt(path, name = t, passphrase = passphrase))
  })
}

res <- list()
if (mode == "r_write") {
  creds <- data.frame(
    user = c("fanny", "victor"),
    password = c("azerty12", "12345A"),
    admin = c(FALSE, TRUE),
    email = c("fanny@mail.com", "élodie@mail.com"),
    stringsAsFactors = FALSE
  )
  for (p in list(list(f = "r_enc.sqlite", pass = "secret"), list(f = "r_plain.sqlite", pass = NULL))) {
    path <- file.path(target, p$f)
    create_db(credentials_data = creds, sqlite_path = path, passphrase = p$pass)
    pm <- read_db_decrypt(path, "pwd_mngt", p$pass)
    pm$n_wrong_pwd[pm$user == "victor"] <- 2
    write_db_encrypt(path, pm, "pwd_mngt", p$pass)
    logs <- data.frame(
      user = "fanny", server_connected = "2026-10-06 10:00:00", token = "tok1",
      logout = NA_character_, app = "demo", status = "Success", stringsAsFactors = FALSE
    )
    write_db_encrypt(path, logs, "logs", p$pass)
  }
  res$enc <- dump_db(file.path(target, "r_enc.sqlite"), "secret")
  res$plain <- dump_db(file.path(target, "r_plain.sqlite"), NULL)
} else if (mode == "r_read") {
  res$before <- dump_db(target, "secret")
  res$login_ok <- check_credentials(target, passphrase = "secret")("fanny", "azerty12")$result
  res$login_bad <- check_credentials(target, passphrase = "secret")("fanny", "nope")$result
  pm <- read_db_decrypt(target, "pwd_mngt", "secret")
  pm$must_change[pm$user == "victor"] <- "TRUE"
  write_db_encrypt(target, pm, "pwd_mngt", "secret")
} else {
  stop("unknown mode")
}

writeLines(toJSON(res, auto_unbox = TRUE, null = "null", na = "null", pretty = TRUE,
                  dataframe = "rows"), out_file, useBytes = TRUE)
cat("oracle db_compat ecrit ->", out_file, "\n")
