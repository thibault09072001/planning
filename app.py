import streamlit as st
import pandas as pd
import io
import math
from ortools.sat.python import cp_model
from datetime import datetime, timedelta

# --- 1. CONFIGURATION DE L'INTERFACE ---
st.set_page_config(page_title="Optimisation Planning EHPAD", page_icon="🏥", layout="wide")
st.title("🏥 Système d'Ajustement des Ressources Humaines")
st.markdown("Génération de planning sous contraintes conventionnelles et optimisation de la charge de travail.")

# --- 2. PARAMÈTRES D'ENTRÉE ---
col1, col2 = st.columns([1, 3])
with col1:
    st.subheader("Paramètres de session")
    nb_semaines = st.number_input("Durée du cycle (semaines) :", min_value=2, max_value=12, value=4)
    date_debut = st.date_input("Date d'effet (Lundi) :", value=datetime.today())

with col2:
    st.subheader("Registre du Personnel & Absences")
    st.caption("Format des absences : '01/05' ou '01/05-05/05'. Les colonnes vides sont ignorées.")
    
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
    
    # Nettoyage des données d'entrée
    df_equipe['Contrat (%)'] = pd.to_numeric(df_equipe['Contrat (%)'], errors='coerce')
    df_equipe = df_equipe.dropna(subset=['Nom', 'Contrat (%)'])
    
    noms_titulaires = df_equipe["Nom"].tolist()
    valeurs_contrats = df_equipe["Contrat (%)"].tolist()
    absences_declarees = df_equipe["Congés / Absences"].tolist()
    
    # Intégration de ressources de remplacement (15 unités disponibles)
    noms_complets = noms_titulaires + [f"REMPLAÇANT {i+1}" for i in range(15)]
    nb_titulaires = len(noms_titulaires)
    total_effectif = len(noms_complets)
    
    with st.spinner("Analyse des contraintes réglementaires et optimisation des flux..."):
        jours_cycle = nb_semaines * 7
        postes = ['M', 'A', 'C']
        model = cp_model.CpModel()
        
        # Définition des variables de décision
        x = {}
        for e in range(total_effectif):
            for d in range(jours_cycle):
                for p in postes:
                    x[(e, d, p)] = model.NewBoolVar(f'staff_{e}_{d}_{p}')
        
        # --- CONTRAINTES RELATIVES AUX TITULAIRES ---
        for e in range(nb_titulaires):
            # Respect de la quotité de travail
            charge_max = int((valeurs_contrats[e] / 100) * 5 * nb_semaines)
            model.Add(sum(x[(e, d, p)] for d in range(jours_cycle) for p in postes) <= charge_max)
            
            # Unité de poste quotidienne
            for d in range(jours_cycle):
                model.AddAtMostOne(x[(e, d, p)] for p in postes)
            
            # Temps de repos minimum (Interdiction Après-midi -> Matin)
            for d in range(jours_cycle - 1):
                model.AddImplication(x[(e, d, 'A')], x[(e, d+1, 'M')].Not())
            
            # Gestion des cycles de week-end
            indicateurs_we = []
            for w in range(nb_semaines):
                sat, sun = w * 7 + 5, w * 7 + 6
                actif_we = model.NewBoolVar(f'actif_we_{e}_{w}')
                for p in postes:
                    model.Add(x[(e, sat, p)] == x[(e, sun, p)]) # Continuité du bloc WE
                
                model.AddMaxEquality(actif_we, [x[(e, sat, p)] for p in postes])
                indicateurs_we.append(actif_we)
                
                # Rythme hebdomadaire 6j / 4j
                periode = range(w * 7, w * 7 + 7)
                charge_hebdo = sum(x[(e, d, p)] for d in periode for p in postes)
                model.Add(charge_hebdo <= 4 + (2 * actif_we))

            # Interdiction de week-ends consécutifs
            for w in range(nb_semaines - 1):
                model.Add(indicateurs_we[w] + indicateurs_we[w+1] <= 1)

            # Sanctuarisation des absences
            indices_abs = extraire_indices_absences(absences_declarees[e], date_debut, jours_cycle)
            for d in indices_abs:
                for p in postes: model.Add(x[(e, d, p)] == 0)

        # --- CONTRAINTES RELATIVES AUX REMPLAÇANTS ---
        for e in range(nb_titulaires, total_effectif):
            for d in range(jours_cycle):
                model.AddAtMostOne(x[(e, d, p)] for p in postes)
                
                # Continuité Week-end : le même remplaçant travaille samedi et dimanche
                if d % 7 == 5: # Samedi
                    for p in postes:
                        model.Add(x[(e, d, p)] == x[(e, d+1, p)])
            
            # Note : Les remplaçants n'ont pas la contrainte 'A' -> 'M'

        # --- QUOTAS DE SERVICE ---
        for d in range(jours_cycle):
            is_we = (d % 7 >= 5)
            m_target, a_target, c_target = (6, 3, 2) if is_we else (8, 4, 1)
            model.Add(sum(x[(e, d, 'M')] for e in range(total_effectif)) == m_target)
            model.Add(sum(x[(e, d, 'A')] for e in range(total_effectif)) == a_target)
            model.Add(sum(x[(e, d, 'C')] for e in range(total_effectif)) == c_target)

        # --- OPTIMISATION ---
        # Priorité à l'utilisation des ressources internes (Titulaires)
        poids_titulaire = sum(x[(e, d, p)] for e in range(nb_titulaires) for d in range(jours_cycle) for p in postes)
        poids_remplacant = sum(x[(e, d, p)] for e in range(nb_titulaires, total_effectif) for d in range(jours_cycle) for p in postes)
        model.Maximize(poids_titulaire * 10 - poids_remplacant)

        solver = cp_model.CpSolver()
        statut = solver.Solve(model)

        if statut in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
            st.success("Planning généré conformément aux contraintes de repos.")
            
            resultats, noms_utilises = [], []
            for e in range(total_effectif):
                total_activite = sum(solver.Value(x[(e, d, p)]) for d in range(jours_cycle) for p in postes)
                if e < nb_titulaires or total_activite > 0:
                    ligne_planning = []
                    for d in range(jours_cycle):
                        valeur = "Repos"
                        for p in postes:
                            if solver.Value(x[(e, d, p)]) == 1: valeur = p
                        ligne_planning.append(valeur)
                    resultats.append(ligne_planning)
                    noms_utilises.append(noms_complets[e])

            df_final = pd.DataFrame(resultats, columns=[(date_debut + timedelta(days=i)).strftime('%a %d/%m') for i in range(jours_cycle)], index=noms_utilises)
            
            # --- GÉNÉRATION EXCEL ---
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                df_final.to_excel(writer, sheet_name='Planning_Operationnel')
                wb, ws = writer.book, writer.sheets['Planning_Operationnel']
                
                # Formats de cellule
                fmt_m = wb.add_format({'bg_color': '#D4EFDF', 'align': 'center', 'border': 1})
                fmt_a = wb.add_format({'bg_color': '#FCF3CF', 'align': 'center', 'border': 1})
                fmt_c = wb.add_format({'bg_color': '#FADBD8', 'align': 'center', 'border': 1})
                fmt_remp = wb.add_format({'bg_color': '#E67E22', 'font_color': '#FFFFFF', 'bold': True, 'border': 1})
                fmt_we = wb.add_format({'bg_color': '#F2F4F4', 'border': 1})
                
                ws.set_column('A:A', 30)
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
            
            st.download_button("📥 EXTRAIRE LE PLANNING (EXCEL)", buffer.getvalue(), f"Planning_RH_{date_debut}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        else:
            st.error("Aucune solution compatible avec les contraintes actuelles. Veuillez réviser les absences ou augmenter les ressources.")
