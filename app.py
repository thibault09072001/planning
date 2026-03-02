import streamlit as st
import pandas as pd
import io
import math
from ortools.sat.python import cp_model
from datetime import datetime, timedelta

# --- 1. CONFIGURATION DE LA PAGE WEB ---
st.set_page_config(page_title="Générateur Planning EHPAD", page_icon="🏥", layout="wide")
st.title("🏥 Générateur de Planning Soignants (IA)")
st.markdown("Outil de planification sous contraintes mathématiques. **Garantit le respect des repos, limite les week-ends consécutifs et équilibre la charge de travail.**")

# --- 2. INTERFACE UTILISATEUR (ENTRÉES) ---
col1, col2 = st.columns([1, 2])

with col1:
    st.subheader("⚙️ Paramètres")
    nb_semaines = st.number_input("Nombre de semaines :", min_value=2, max_value=12, value=4)
    # Date par défaut réglée sur aujourd'hui
    date_debut = st.date_input("Date de début (Lundi) :", value=datetime.today())

with col2:
    st.subheader("👥 Équipe Soignante")
    st.markdown("Ajoute ou supprime des soignants. L'IA adaptera les règles de week-end à l'effectif.")
    
    # Équipe de base de l'EHPAD (15 à 100% et 3 à 80%)
    data_base = pd.DataFrame({
        "Nom": [f"Soignant 100% (n°{i+1})" for i in range(15)] + [f"Soignant 80% (n°{i+1})" for i in range(3)],
        "Contrat (%)": [100]*15 + [80]*3
    })
    # Tableau éditable sur le site web
    df_equipe = st.data_editor(data_base, num_rows="dynamic", use_container_width=True)


# --- 3. LE MOTEUR IA & GÉNÉRATION EXCEL ---
if st.button("🚀 GÉNÉRER LE PLANNING OPTIMISÉ", type="primary", use_container_width=True):
    
    # 🛡️ BOUCLIER DE SÉCURITÉ : Nettoyage des données du tableau
    df_equipe['Contrat (%)'] = pd.to_numeric(df_equipe['Contrat (%)'], errors='coerce')
    df_equipe = df_equipe.dropna(subset=['Nom', 'Contrat (%)'])
    df_equipe = df_equipe[df_equipe['Contrat (%)'] > 0]
    
    noms_salaries = df_equipe["Nom"].tolist()
    contrats = df_equipe["Contrat (%)"].tolist()
    total_salaries = len(noms_salaries)
    
    # Vérification : Reste-t-il des gens pour travailler ?
    if total_salaries == 0:
        st.error("⚠️ Impossible de calculer : le tableau de l'équipe est vide ou mal rempli.")
    else:
        with st.spinner("L'IA calcule la répartition parfaite et applique les règles de repos..."):
            
            jours = nb_semaines * 7
            
            # Calculs des jours
            max_jours = [int((c / 100) * 5 * nb_semaines) for c in contrats]
            shifts = ['M', 'A', 'C']
            
            # Calcul dynamique des week-ends
            total_we_needs = 11 * nb_semaines
            min_we = math.floor(total_we_needs / total_salaries)
            max_we = math.ceil(total_we_needs / total_salaries)
            
            model = cp_model.CpModel()
            x = {}
            for e in range(total_salaries):
                for d in range(jours):
                    for s in shifts:
                        x[(e, d, s)] = model.NewBoolVar(f'shift_{e}_{d}_{s}')
                        
            # --- RÈGLES ET CONTRAINTES DE BASE ---
            for e in range(total_salaries):
                for d in range(jours):
                    model.AddAtMostOne(x[(e, d, s)] for s in shifts) # 1 poste/jour maximum
                
                model.Add(sum(x[(e, d, s)] for d in range(jours) for s in shifts) == max_jours[e]) # Contrat exact
                
                for d in range(jours - 1):
                    model.AddImplication(x[(e, d, 'A')], x[(e, d+1, 'M')].Not()) # Interdit : Après-midi -> Matin
                
                for w in range(nb_semaines):
                    sat, sun = w * 7 + 5, w * 7 + 6
                    for s in shifts:
                        model.Add(x[(e, sat, s)] == x[(e, sun, s)]) # Week-end en bloc (Sam=Dim)

            # --- RÈGLE STRICTE DES WEEK-ENDS (CONFORT ABSOLU) ---
            for e in range(total_salaries):
                we_travailles = []
                for w in range(nb_semaines):
                    sat = w * 7 + 5
                    travail_ce_we = model.NewBoolVar(f'we_{e}_{w}')
                    model.AddMaxEquality(travail_ce_we, [x[(e, sat, s)] for s in shifts])
                    we_travailles.append(travail_ce_we)
                
                # Limite globale d'équité
                total_we = sum(we_travailles)
                model.Add(total_we <= max_we)
                model.Add(total_we >= min_we)
                
                # Interdiction formelle des 3 WE de suite (Sécurité supplémentaire)
                if nb_semaines >= 3:
                    for w in range(nb_semaines - 2):
                        model.Add(we_travailles[w] + we_travailles[w+1] + we_travailles[w+2] <= 2)
                
                # Calcul des week-ends consécutifs
                consecutifs = []
                if nb_semaines > 1:
                    for w in range(nb_semaines - 1):
                        consec = model.NewBoolVar(f'consec_{e}_{w}')
                        model.Add(consec <= we_travailles[w])
                        model.Add(consec <= we_travailles[w+1])
                        model.Add(consec >= we_travailles[w] + we_travailles[w+1] - 1)
                        consecutifs.append(consec)
                    
                    total_consec = sum(consecutifs)
                    
                    # RÈGLE D'OR : 
                    # Si 2 WE (ou moins) -> 0 week-end consécutif (Pause obligatoire)
                    b_2 = model.NewBoolVar(f'b2_{e}')
                    model.Add(total_we <= 2).OnlyEnforceIf(b_2)
                    model.Add(total_we > 2).OnlyEnforceIf(b_2.Not())
                    model.Add(total_consec == 0).OnlyEnforceIf(b_2)
                    
                    # Si 3 WE -> EXACTEMENT 1 week-end consécutif (Interdit d'en faire 3 de suite)
                    b_3 = model.NewBoolVar(f'b3_{e}')
                    model.Add(total_we == 3).OnlyEnforceIf(b_3)
                    model.Add(total_we != 3).OnlyEnforceIf(b_3.Not())
                    model.Add(total_consec == 1).OnlyEnforceIf(b_3)

            # --- QUOTAS DE LA MAISON DE RETRAITE ---
            for d in range(jours):
                is_we = (d % 7 >= 5)
                if is_we:
                    model.Add(sum(x[(e, d, 'M')] for e in range(total_salaries)) >= 6) # >= pour les renforts
                    model.Add(sum(x[(e, d, 'A')] for e in range(total_salaries)) == 3)
                    model.Add(sum(x[(e, d, 'C')] for e in range(total_salaries)) == 2)
                else:
                    model.Add(sum(x[(e, d, 'M')] for e in range(total_salaries)) >= 8) # >= pour les renforts
                    model.Add(sum(x[(e, d, 'A')] for e in range(total_salaries)) == 4)
                    model.Add(sum(x[(e, d, 'C')] for e in range(total_salaries)) == 1)

            # --- LANCEMENT DU CALCULATEUR ---
            solver = cp_model.CpSolver()
            solver.parameters.max_time_in_seconds = 60.0 # L'IA a 60 sec
            status = solver.Solve(model)

            if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
                st.success(f"✅ Planning généré ! Charge répartie entre {min_we} et {max_we} week-ends par soignant.")
                
                # --- CRÉATION DU FICHIER EXCEL ---
                planning_data = []
                audit_data = []
                
                for e in range(total_salaries):
                    ligne = []
                    j_travailles = 0
                    we_travailles = 0
                    mem_we = [] # Pour recompter l'audit
                    
                    for d in range(jours):
                        poste = "Repos"
                        for s in shifts:
                            if solver.Value(x[(e, d, s)]) == 1:
                                poste = s
                                j_travailles += 1
                        
                        if d % 7 == 5: # Samedi : on regarde si travaillé pour l'audit
                            travail_we = 1 if poste != "Repos" else 0
                            mem_we.append(travail_we)
                            if travail_we == 1:
                                we_travailles += 1
                                
                        ligne.append(poste)
                    planning_data.append(ligne)
                    
                    # Audit final des week-ends consécutifs
                    consec_count = 0
                    for w in range(nb_semaines - 1):
                        if mem_we[w] == 1 and mem_we[w+1] == 1:
                            consec_count += 1
                            
                    msg_audit = f"✅ {j_travailles}/{max_jours[e]} jrs | {we_travailles} WE"
                    if consec_count > 0:
                        msg_audit += f" | ⚠️ {consec_count} enchaînement(s) WE"
                    audit_data.append(msg_audit)

                colonnes = [(date_debut + timedelta(days=i)).strftime('%a %d/%m') for i in range(jours)]
                df = pd.DataFrame(planning_data, columns=colonnes, index=noms_salaries)
                df['AUDIT RÈGLES'] = audit_data
                
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                    df.to_excel(writer, sheet_name='Planning')
                    workbook = writer.book
                    worksheet = writer.sheets['Planning']
                    
                    # Design professionnel
                    format_M = workbook.add_format({'bg_color': '#D4EFDF', 'font_color': '#145A32', 'align': 'center'})
                    format_A = workbook.add_format({'bg_color': '#FCF3CF', 'font_color': '#9A7D0A', 'align': 'center'})
                    format_C = workbook.add_format({'bg_color': '#FADBD8', 'font_color': '#78281F', 'align': 'center'})
                    format_R = workbook.add_format({'font_color': '#BFC9CA', 'align': 'center'})
                    format_WE = workbook.add_format({'bg_color': '#EBEDEF', 'align': 'center'})
                    format_Audit = workbook.add_format({'font_color': '#2C3E50', 'bold': True})
                    
                    worksheet.set_column('A:A', 25) 
                    worksheet.set_column(1, jours, 10) 
                    worksheet.set_column(jours + 1, jours + 1, 45) 
                    
                    for row_num in range(total_salaries):
                        for col_num in range(jours):
                            val = df.iloc[row_num, col_num]
                            cell_format = format_M if val == 'M' else format_A if val == 'A' else format_C if val == 'C' else format_R
                            if val == 'Repos' and (col_num % 7 >= 5): cell_format = format_WE
                            worksheet.write(row_num + 1, col_num + 1, val, cell_format)
                        worksheet.write(row_num + 1, jours + 1, df.iloc[row_num, jours], format_Audit)

                st.download_button(
                    label="📥 TÉLÉCHARGER LE FICHIER EXCEL PRO",
                    data=output.getvalue(),
                    file_name=f"Planning_EHPAD_{date_debut.strftime('%d-%m-%Y')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    type="primary"
                )
            else:
                st.error("❌ Impossible de générer le planning. Il n'y a pas assez de soignants pour respecter les repos obligatoires de week-end ou les quotas.")
