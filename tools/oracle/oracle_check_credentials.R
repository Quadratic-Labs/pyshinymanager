#!/usr/bin/env Rscript
# Oracle differentiel pour check_credentials (chemin data.frame = coeur check_credentials_df).
# Joue des scenarios deterministes et renvoie les 3 booleens du contrat (result/expired/
# authorized). L'appname est fixe via options() pour etre reproductible cross-environnement.
#
# Usage : Rscript oracle_check_credentials.R <out.json>

suppressWarnings(suppressMessages({ library(jsonlite); library(scrypt) }))

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 1) stop("Usage: Rscript oracle_check_credentials.R <out.json>")
out_file <- args[[1]]

this_dir <- dirname(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)))
if (length(this_dir) == 0 || !nzchar(this_dir)) this_dir <- "."
src <- normalizePath(file.path(this_dir, "..", "..", "..", "source", Sys.getenv("SM_SOURCE", "shinymanager-1.1.1.1")), mustWork = TRUE)
suppressWarnings(suppressMessages(pkgload::load_all(src, quiet = TRUE, export_all = TRUE)))

options(shinymanager.application = "myapp")

triple <- function(a) list(result = a$result, expired = a$expired, authorized = a$authorized)

yesterday <- as.character(Sys.Date() - 1)
tomorrow <- as.character(Sys.Date() + 1)
hashed <- scrypt::hashPassword("azerty")

res <- list()

# 1-3. clair
clear <- data.frame(user = "fanny", password = "azerty", stringsAsFactors = FALSE)
res$clear_ok <- triple(check_credentials(clear)("fanny", "azerty"))
res$clear_wrong <- triple(check_credentials(clear)("fanny", "wrong"))
res$ghost <- triple(check_credentials(clear)("ghost", "x"))

# 4. hash
hdf <- data.frame(user = "fanny", password = hashed, is_hashed_password = TRUE, stringsAsFactors = FALSE)
res$hashed_ok <- triple(check_credentials(hdf)("fanny", "azerty"))
res$hashed_wrong <- triple(check_credentials(hdf)("fanny", "nope"))

# 5-6. expire
exp_past <- data.frame(user = "u", password = "p", expire = yesterday, stringsAsFactors = FALSE)
res$expire_past <- triple(check_credentials(exp_past)("u", "p"))
exp_future <- data.frame(user = "u", password = "p", expire = tomorrow, stringsAsFactors = FALSE)
res$expire_future <- triple(check_credentials(exp_future)("u", "p"))

# 7-8. applications
appdf <- data.frame(user = "u", password = "p", applications = "myapp;other", stringsAsFactors = FALSE)
res$app_match <- triple(check_credentials(appdf)("u", "p"))
appdf2 <- data.frame(user = "u", password = "p", applications = "other;nope", stringsAsFactors = FALSE)
res$app_nomatch <- triple(check_credentials(appdf2)("u", "p"))

res$hashed_value <- hashed

writeLines(toJSON(res, auto_unbox = TRUE, null = "null", na = "null", pretty = TRUE),
           out_file, useBytes = TRUE)
cat("oracle check_credentials ecrit ->", out_file, "\n")
