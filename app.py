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
    date_debut = st.date_input("Date de début (Lundi) :", value=datetime.today())

with col2:
    st.subheader("👥 Équipe Soignante")
    st.markdown("Ajoute ou modifie les soignants. L'IA calculera automatiquement l'équité des week-ends.")
    # Équipe de base de l'EHPAD (15 à 100% et 3 à 80%)
    data_base = pd.DataFrame({
        "Nom": [f"Soignant 100% (n°{i+1})" for i in range(15)] + [f"Soignant 80% (n°{i+1})" for i in range(3)],
        "Contrat (%)": [100]*15 + [80]*3
    })
    # Tableau éditable sur le site web
    df_equipe = st.data_editor(data_base, num_rows="dynamic", use_container_width=True)

# --- 3. LE MOTEUR IA & GÉNÉRATION EXCEL ---
if st.button("🚀 GÉNÉRER LE PLANNING OPTIMISÉ", type="primary", use_container_width=True):
    with st.spinner("L'IA calcule la répartition optimale et chasse les week-ends consécutifs..."):
        
        jours = nb_semaines * 7
        noms_salaries = df_equipe["Nom"].tolist()
        contrats = df_equipe["Contrat (%)"].tolist()
        total_salaries = len(noms_salaries)
        
        # Calculs des jours et week-ends
        max_jours = [int((c / 100) * 5 * nb_semaines) for c in contrats]
        shifts = ['M', 'A', 'C']
        
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
            
            model.Add(sum(x[(e, d, s)] for d in range(jours) for s in shifts) == max_jours[e]) # Respect du contrat exact
            
            for d in range(jours - 1):
                model.AddImplication(x[(e, d, 'A')], x[(e, d+1, 'M')].Not()) # Interdit : Après-midi suivi de Matin
            
            for w in range(nb_semaines):
                sat, sun = w * 7 + 5, w * 7 + 6
                for s in shifts:
                    model.Add(x[(e, sat, s)] == x[(e, sun, s)]) # Week-end en bloc (Samedi = Dimanche)

        # --- RÈGLE D'ÉQUITÉ ET DE CONFORT DES WEEK-ENDS ---
        we_travailles_global = [] # Pour mémoriser les WE de tout le monde
        
        for e in range(total_salaries):
            we_travailles = []
            for w in range(nb_semaines):
                sat = w * 7 + 5
                travail_ce_we = model.NewBoolVar(f'we_{e}_{w}')
                model.AddMaxEquality(travail_ce_we, [x[(e, sat, s)] for s in shifts])
                we_travailles.append(travail_ce_we)
            
            # Limite mathématique dynamique
            model.Add(sum(we_travailles) <= max_we)
            model.Add(sum(we_travailles) >= min_we)
            
            # 🛑 INTERDIT STRICTEMENT DE FAIRE 3 WEEK-ENDS DE SUITE
            if nb_semaines >= 3:
                for w in range(nb_semaines - 2):
                    model.Add(we_travailles[w] + we_travailles[w+1] + we_travailles[w+2] <= 2)
                    
            we_travailles_global.append(we_travailles)

        # 🧠 OPTIMISATION : MINIMISER LES 2 WEEK-ENDS CONSÉCUTIFS POUR LE CONFORT
        consecutifs = []
        for e in range(total_salaries):
            for w in range(nb_semaines - 1):
                consec = model.NewBoolVar(f'consec_{e}_{w}')
                # Logique IA : Si WE 1 et WE 2 sont travaillés, la pénalité "consec" s'active
                model.AddBoolOr([we_travailles_global[e][w].Not(), we_travailles_global[e][w+1].Not(), consec])
                consecutifs.append(consec)
        
        # Ordre à l'IA : Réduis cette pénalité au maximum !
        model.Minimize(sum(consecutifs))

        # --- QUOTAS DE LA MAISON DE RETRAITE ---
        for d in range(jours):
            is_we = (d % 7 >= 5)
            if is_we:
                model.Add(sum(x[(e, d, 'M')] for e in range(total_salaries)) >= 6)
                model.Add(sum(x[(e, d, 'A')] for e in range(total_salaries)) == 3)
                model.Add(sum(x[(e, d, 'C')] for e in range(total_salaries)) == 2)
            else:
                model.Add(sum(x[(e, d, 'M')] for e in range(total_salaries)) >= 8)
                model.Add(sum(x[(e, d, 'A')] for e in range(total_salaries)) == 4)
                model.Add(sum(x[(e, d, 'C')] for e in range(total_salaries)) == 1)

        # --- LANCEMENT DU CALCULATEUR ---
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 60.0 # L'IA a 60 secondes pour trouver la perfection
        status = solver.Solve(model)

        if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
            st.success(f"✅ Planning généré ! L'IA a réparti la charge entre {min_we} et {max_we} week-ends par soignant.")
            if status == cp_model.OPTIMAL:
                st.info("⭐ L'IA a trouvé la solution la plus confortable possible pour éviter les week-ends consécutifs !")
            
            # --- CRÉATION DU FICHIER EXCEL ---
            planning_data = []
            audit_data = []
            
            for e in range(total_salaries):
                ligne = []
                j_travailles = 0
                we_travailles = 0
                for d in range(jours):
                    poste = "Repos"
                    for s in shifts:
                        if solver.Value(x[(e, d, s)]) == 1:
                            poste = s
                            j_travailles += 1
                            if d % 7 == 6: 
                                we_travailles += 1
                    ligne.append(poste)
                planning_data.append(ligne)
                
                # Vérification des consécutifs pour l'audit
                consec_count = 0
                for w in range(nb_semaines - 1):
                    if solver.Value(we_travailles_global[e][w]) == 1 and solver.Value(we_travailles_global[e][w+1]) == 1:
                        consec_count += 1
                        
                msg_audit = f"✅ {j_travailles}/{max_jours[e]} jrs | {we_travailles} WE"
                if consec_count > 0:
                    msg_audit += f" | ⚠️ {consec_count} WE de suite"
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
            st.error("❌ Impossible de trouver un planning. Il n'y a pas assez de soignants pour couvrir les besoins ou les contrats sont incompatibles.")
