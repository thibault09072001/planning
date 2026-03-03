import streamlit as st
import pandas as pd
import io
import math
from ortools.sat.python import cp_model
from datetime import datetime, timedelta

# --- 1. CONFIGURATION DE L'INTERFACE ---
st.set_page_config(page_title="Optimisation Planning EHPAD", page_icon="🏥", layout="wide")
st.title("🏥 Système d'Ajustement des Ressources Humaines")
st.markdown("Contraintes appliquées : Max 4 jours consécutifs, 9 titulaires + 2 remplaçants par week-end.")

# --- 2. PARAMÈTRES D'ENTRÉE ---
col1, col2 = st.columns([1, 3])
with col1:
    st.subheader("Paramètres de session")
    nb_semaines = st.number_input("Durée du cycle (semaines) :", min_value=2, max_value=12, value=4)
    
    # Calcul du lundi de la semaine en cours par défaut
    aujourdhui = datetime.today()
    lundi_par_defaut = aujourdhui - timedelta(days=aujourdhui.weekday())
    
    date_debut = st.date_input("Date d'effet (Lundi IMPÉRATIF) :", value=lundi_par_defaut)
    
    # SÉCURITÉ : Blocage si la date n'est pas un Lundi
    if date_debut.weekday() != 0:
        st.error("🛑 Erreur : La date de début doit obligatoirement être un Lundi pour garantir l'alignement des week-ends.")
        st.stop()

with col2:
    st.subheader("Registre du Personnel & Absences")
    st.caption("Format des absences : '01/05' ou '01/05-05/05'.")
    
    data_base = pd.DataFrame({
        "Nom": [f"Salarié {i+1}" for i in range(15)] + [f"Salarié {i+16}" for i in range(3)],
        "Contrat (%)": [100]*15 + [80]*3,
        "Congés / Absences": [""] * 18
    })
    df_equipe = st.data_editor(data_base, num_rows="dynamic", use_container_width=True)

# --- UTILITAIRE DE TRAITEMENT DES DATES ---
def extraire_indices_absences(texte, date_ref, total_jours):
    indices = []
    if not texte or pd.isna(texte): return indices
    segments = str(texte).replace(' ', '').split(',')
    for segment in segments:
        try:
            if '-' in segment:
                d1_s, d2_s = segment.split('-')
                d1 = datetime.strptime(d1_s + f"/{date_ref.year}", "%d/%m/%Y").date()
                d2 = datetime.strptime(d2_s + f"/{date_ref.year}", "%d/%m/%Y").date()
                for i in range((d2 - d1).days + 1):
                    ecart = (d1 + timedelta(days=i) - date_ref).days
                    if 0 <= ecart < total_jours: indices.append(ecart)
            else:
                cible = datetime.strptime(segment + f"/{date_ref.year}", "%d/%m/%Y").date()
                ecart = (cible - date_ref).days
                if 0 <= ecart < total_jours: indices.append(ecart)
        except: continue
    return list(set(indices))

# --- 3. MOTEUR DE RÉSOLUTION ---
if st.button("GÉNÉRER LE PLANNING OPÉRATIONNEL", type="primary", use_container_width=True):
    
    df_equipe['Contrat (%)'] = pd.to_numeric(df_equipe['Contrat (%)'], errors='coerce')
    df_equipe = df_equipe.dropna(subset=['Nom', 'Contrat (%)'])
    
    noms_titulaires = df_equipe["Nom"].tolist()
    valeurs_contrats = df_equipe["Contrat (%)"].tolist()
    absences_declarees = df_equipe["Congés / Absences"].tolist()
    
    # EXACTEMENT 2 REMPLAÇANTS POUR ÉVITER LES LIGNES VIDES
    noms_complets = noms_titulaires + ["REMPLAÇANT 1", "REMPLAÇANT 2"]
    nb_titulaires = len(noms_titulaires)
    total_effectif = len(noms_complets)
    
    with st.spinner("Analyse des contraintes et répartition des effectifs..."):
        jours_cycle = nb_semaines * 7
        postes = ['M', 'A', 'C']
        model = cp_model.CpModel()
        
        x = {}
        for e in range(total_effectif):
            for d in range(jours_cycle):
                for p in postes:
                    x[(e, d, p)] = model.NewBoolVar(f'staff_{e}_{d}_{p}')
        
        # --- CONTRAINTES RELATIVES AUX TITULAIRES ---
        for e in range(nb_titulaires):
            charge_max = int((valeurs_contrats[e] / 100) * 5 * nb_semaines)
            model.Add(sum(x[(e, d, p)] for d in range(jours_cycle) for p in postes) <= charge_max)
            
            for d in range(jours_cycle):
                model.AddAtMostOne(x[(e, d, p)] for p in postes)
            
            for d in range(jours_cycle - 1):
                model.AddImplication(x[(e, d, 'A')], x[(e, d+1, 'M')].Not())
            
            for d in range(jours_cycle - 4):
                model.Add(sum(x[(e, d+i, p)] for i in range(5) for p in postes) <= 4)
            
            indicateurs_we = []
            for w in range(nb_semaines):
                sat, sun = w * 7 + 5, w * 7 + 6
                actif_we = model.NewBoolVar(f'actif_we_{e}_{w}')
                for p in postes:
                    model.Add(x[(e, sat, p)] == x[(e, sun, p)]) 
                
                model.AddMaxEquality(actif_we, [x[(e, sat, p)] for p in postes])
                indicateurs_we.append(actif_we)
                
                periode = range(w * 7, w * 7 + 7)
                charge_hebdo = sum(x[(e, d, p)] for d in periode for p in postes)
                model.Add(charge_hebdo <= 4 + (2 * actif_we))

            for w in range(nb_semaines - 1):
                model.Add(indicateurs_we[w] + indicateurs_we[w+1] <= 1)

            indices_abs = extraire_indices_absences(absences_declarees[e], date_debut, jours_cycle)
            for d in indices_abs:
                for p in postes: model.Add(x[(e, d, p)] == 0)

        # --- CONTRAINTES RELATIVES AUX REMPLAÇANTS ---
        for e in range(nb_titulaires, total_effectif):
            for d in range(jours_cycle):
                model.AddAtMostOne(x[(e, d, p)] for p in postes)
                if d % 7 == 5: 
                    for p in postes:
                        model.Add(x[(e, d, p)] == x[(e, d+1, p)])

        # --- QUOTAS DE SERVICE ---
        for d in range(jours_cycle):
            is_we = (d % 7 >= 5)
            m_target, a_target, c_target = (6, 3, 2) if is_we else (8, 4, 1)
            model.Add(sum(x[(e, d, 'M')] for e in range(total_effectif)) == m_target)
            model.Add(sum(x[(e, d, 'A')] for e in range(total_effectif)) == a_target)
            model.Add(sum(x[(e, d, 'C')] for e in range(total_effectif)) == c_target)
            
            if is_we:
                model.Add(sum(x[(e, d, p)] for e in range(nb_titulaires) for p in postes) == 9)
                model.Add(sum(x[(e, d, p)] for e in range(nb_titulaires, total_effectif) for p in postes) == 2)

        # --- OPTIMISATION ---
        poids_titulaire = sum(x[(e, d, p)] for e in range(nb_titulaires) for d in range(jours_cycle) for p in postes)
        poids_remplacant = sum(x[(e, d, p)] for e in range(nb_titulaires, total_effectif) for d in range(jours_cycle) for p in postes)
        model.Maximize(poids_titulaire * 10 - poids_remplacant)

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 45.0
        statut = solver.Solve(model)

        if statut in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
            st.success("Planning généré. Les lignes d'audit ont été ajoutées.")
            
            resultats, noms_utilises, audit_data = [], [], []
            for e in range(total_effectif):
                total_activite = sum(solver.Value(x[(e, d, p)]) for d in range(jours_cycle) for p in postes)
                if e < nb_titulaires or total_activite > 0:
                    ligne_planning = []
                    jours_travailles = 0
                    
                    # Construction de la ligne de planning
                    for d in range(jours_cycle):
                        valeur = "Repos"
                        for p in postes:
                            if solver.Value(x[(e, d, p)]) == 1: 
                                valeur = p
                                jours_travailles += 1
                        ligne_planning.append(valeur)
                    
                    # Audit : Calcul des enchaînements A -> M
                    enchainements_am = 0
                    for d in range(jours_cycle - 1):
                        if ligne_planning[d] == 'A' and ligne_planning[d+1] == 'M':
                            enchainements_am += 1
                            
                    # Stockage des données
                    resultats.append(ligne_planning)
                    noms_utilises.append(noms_complets[e])
                    audit_data.append(f"{jours_travailles}j | {enchainements_am} A->M")

            # Création du DataFrame
            colonnes = [(date_debut + timedelta(days=i)).strftime('%a %d/%m') for i in range(jours_cycle)]
            df_final = pd.DataFrame(resultats, columns=colonnes, index=noms_utilises)
            df_final['AUDIT'] = audit_data # Ajout de la colonne d'audit
            
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                df_final.to_excel(writer, sheet_name='Planning_Operationnel')
                wb, ws = writer.book, writer.sheets['Planning_Operationnel']
                
                fmt_m = wb.add_format({'bg_color': '#D4EFDF', 'align': 'center', 'border': 1})
                fmt_a = wb.add_format({'bg_color': '#FCF3CF', 'align': 'center', 'border': 1})
                fmt_c = wb.add_format({'bg_color': '#FADBD8', 'align': 'center', 'border': 1})
                fmt_remp = wb.add_format({'bg_color': '#E67E22', 'font_color': '#FFFFFF', 'bold': True, 'border': 1})
                fmt_we = wb.add_format({'bg_color': '#F2F4F4', 'border': 1})
                fmt_audit = wb.add_format({'font_color': '#2C3E50', 'bold': True, 'align': 'center', 'border': 1, 'bg_color': '#EAEDED'})
                
                ws.set_column('A:A', 30)
                ws.set_column(jours_cycle + 1, jours_cycle + 1, 15) # Largeur pour la colonne AUDIT
                
                for r_idx in range(len(noms_utilises)):
                    est_remplacant = "REMPLAÇANT" in noms_utilises[r_idx]
                    for c_idx in range(jours_cycle):
                        val = df_final.iloc[r_idx, c_idx]
                        if est_remplacant and val != "Repos":
                            format_cible = fmt_remp
                        elif val == 'M': format_cible = fmt_m
                        elif val == 'A': format_cible = fmt_a
                        elif val == 'C': format_cible = fmt_c
                        elif c_idx % 7 >= 5: format_cible = fmt_we
                        else: format_cible = None
                        ws.write(r_idx + 1, c_idx + 1, val, format_cible)
                        
                    # Écriture de la cellule d'audit avec son format
                    ws.write(r_idx + 1, jours_cycle + 1, df_final.iloc[r_idx, jours_cycle], fmt_audit)
            
            st.download_button("📥 EXTRAIRE LE PLANNING (EXCEL)", buffer.getvalue(), f"Planning_RH_{date_debut.strftime('%d-%m-%Y')}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        else:
            st.error("Aucune solution compatible. Vérifiez les chevauchements d'absences.")
